"""LyricBridge on Modal — hosted demo backend.

Build order: D1 (image + app + smoke) → D2 (models→Volume) →
D3 (spawn+poll) → D4/D5. See docs/MODAL_DEPLOYMENT.md and docs/MODAL_RULES.md.

RULES (docs/MODAL_RULES.md):
  - App/Volume/Secret names are kebab-case; app = "lyricbridge".
  - Reuse server/app — do NOT copy pipeline code here.
  - Heavy imports (torch, faster-whisper, demucs) go INSIDE function bodies,
    never at module top, so this file imports cleanly locally.
  - All `modal` CLI runs on the OWNER'S machine, with PYTHONPATH=server so the
    local `app` package (server/app) is importable for add_local_python_source.

Run (owner's box):
    PYTHONPATH=server modal run deploy/modal_app.py::smoke
"""

import modal

APP_NAME = "lyricbridge"
app = modal.App(APP_NAME)

# One image shared by the web (CPU) and process_song (GPU) functions.
# requirements.txt pins audio-separator[cpu]; D2/D3 will resolve whether a CUDA
# build is needed when a model actually loads on GPU (import success != GPU works).
image = (
    modal.Image.debian_slim(python_version="3.12")
    # fonts-noto-core ships "Noto Sans Thai" (registers that fontconfig family) and
    # is in Debian; the docs' "fonts-tlwg-sarabun" is not a real package (TLWG has
    # Garuda/Loma/Norasi/Waree, not Sarabun). Noto Sans Thai is also render.py's
    # built-in default, so image font == env == self-host default.
    .apt_install("ffmpeg", "fonts-noto-core")
    .pip_install_from_requirements("server/requirements.txt")  # keep existing pins
    .env(
        {
            "RENDER_FONT": "Noto Sans Thai",  # matches the apt font installed above
            "HF_HOME": "/models/hf",  # whisper CT2 + wav2vec2 aligner cache (D2 Volume)
            "SEPARATION_MODEL_DIR": "/models/audio-separator",  # demucs weights (D2 Volume)
            "TORCH_HOME": "/models/torch",
            # GPU-device defaults baked into the IMAGE (not only the Secret) so a
            # forgotten lyricbridge-config key can't silently drop the pipeline to
            # CPU. The footgun: separate.py defaults SEPARATION_DEVICE to ASR_DEVICE
            # then to "cpu" when BOTH are unset (separate.py:39), so an empty Secret
            # = CPU everywhere. process_song/canary always carry a GPU (gpu=...), so
            # GPU is the correct default. Safe on the shared image's CPU functions
            # (web/render_job/download_models): asr.resolve_device guards every pick
            # with torch.cuda.is_available(), so "cuda"/"auto" downgrade to CPU when
            # no GPU is present. A Secret value still wins (runtime > image), so an
            # explicit SEPARATION_DEVICE=cpu for debugging keeps working.
            "ASR_DEVICE": "auto",          # cuda when available, else cpu
            "SEPARATION_DEVICE": "cuda",   # process_song always has a GPU
        }
    )
    .add_local_python_source("app")  # the existing server/app package — reuse, don't copy
)


@app.function(image=image)
def smoke():
    """D1 acceptance: every server/app module imports inside the image + ffmpeg present."""
    import subprocess

    # All transitive heavy deps (torch, ctranslate2, onnxruntime) must resolve.
    from app import main, separate, render, asr, align, lrc, thai  # noqa: F401

    ff = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout
    print(ff[:80])
    return "ok"


# ──────────────────────────────────────────────────────────────────────────
# D2 — Model weights → Volume
# ──────────────────────────────────────────────────────────────────────────
# All three model families cache under /models (env set in the image):
#   HF_HOME=/models/hf               → whisper CT2 + wav2vec2 aligner
#   SEPARATION_MODEL_DIR=/models/...  → demucs (htdemucs.yaml) weights
#   TORCH_HOME=/models/torch
# ASR_MODEL / ASR_MODEL_REVISION come from the lyricbridge-config Secret so the
# SAME Thai model flows to download_models + process_song + web. asr.py reads
# them at import time, and the import is inside the function body (Secret env is
# injected before the body runs), so the pinned Thai model is what gets cached.
models_vol = modal.Volume.from_name("lyricbridge-models", create_if_missing=True)
config = modal.Secret.from_name("lyricbridge-config")


@app.function(image=image, volumes={"/models": models_vol}, secrets=[config],
              memory=4096, timeout=1800)  # CPU model load needs headroom; no OOM mid-download
def download_models():
    """Populate the Volume once (owner: `PYTHONPATH=server modal run ...::download_models`)."""
    from app import asr, align, separate

    # 1) ASR — pinned Thai whisper CT2 (ASR_MODEL/REVISION from the Secret).
    asr._load_model()
    asr.free_model()
    # 2) wav2vec2 aligner (airesearch/wav2vec2-large-xlsr-53-th); CPU is fine to fetch.
    align._load_align_model("th", "cpu")
    align.free_model()
    # 3) demucs weights (htdemucs.yaml) via a throwaway Separator load.
    from audio_separator.separator import Separator

    Separator(
        output_dir="/tmp",
        output_format="WAV",
        model_file_dir=separate.MODEL_FILE_DIR,
    ).load_model(model_filename=separate.SEPARATION_MODEL)

    models_vol.commit()  # persist writes to the distributed Volume
    return "downloaded"


@app.function(image=image, volumes={"/models": models_vol})
def list_models():
    """D2 acceptance: hf/, audio-separator/, torch/ caches are present & non-empty."""
    import os

    out = {}
    for sub in ("hf", "audio-separator", "torch"):
        path = f"/models/{sub}"
        files = []
        for root, _dirs, names in os.walk(path):
            for n in names:
                files.append(os.path.relpath(os.path.join(root, n), path))
        out[sub] = {"count": len(files), "sample": sorted(files)[:8]}
        print(f"{sub}: {len(files)} files")
        for f in sorted(files)[:8]:
            print(f"    {f}")
    return out


# ──────────────────────────────────────────────────────────────────────────
# D3 — spawn + poll (the heart): GPU process_song + CPU ASGI web app
# ──────────────────────────────────────────────────────────────────────────
# Two containers, no shared disk (MODAL_RULES). The browser talks only to the
# CPU `web` app; `web` spawns the GPU `process_song` and the browser polls.
#
# Data flow across the boundary:
#   - small JSON payload (words/lrc/ass/...) ← process_song RETURN value
#     (cheap to re-fetch via FunctionCall.get on every poll).
#   - m4a stem BYTES → written by the GPU side into `stems` Dict, keyed by
#     job_id, as (bytes, expiry). Keeping bytes OUT of the return value keeps
#     each poll's get() tiny. TTL + hourly sweep = "don't persist user audio".
#   - job_id → Modal call id → `calls` Dict, so GET /jobs/{id} resumes after a
#     browser refresh (frontend stores job_id in localStorage, player.js).
stems = modal.Dict.from_name("lyricbridge-stems", create_if_missing=True)
calls = modal.Dict.from_name("lyricbridge-calls", create_if_missing=True)
progress = modal.Dict.from_name("lyricbridge-progress", create_if_missing=True)
# D5 guardrails: per-IP hourly counters + a single in-flight gauge (queue depth).
ratelimit = modal.Dict.from_name("lyricbridge-ratelimit", create_if_missing=True)
gauges = modal.Dict.from_name("lyricbridge-gauges", create_if_missing=True)
# D5 live control: the $30 kill switch + tunable caps, read PER REQUEST so an
# owner `modal dict put lyricbridge-control ACCEPTING_JOBS 0` takes effect
# immediately — no redeploy, no warm-container env-staleness race (env-from-Secret
# only refreshes when the container cycles). Falls back to env/default when unset.
control = modal.Dict.from_name("lyricbridge-control", create_if_missing=True)
# P3 observability: per-UTC-day counters for /metrics-lite.
metrics = modal.Dict.from_name("lyricbridge-metrics", create_if_missing=True)


def _bump_metric(field):
    """Increment today's counter (submitted/done/error). Shared by web + GPU."""
    import datetime

    day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    rec = metrics.get(day) or {"submitted": 0, "done": 0, "error": 0}
    rec[field] = rec.get(field, 0) + 1
    metrics.put(day, rec)

# Stage → step map MUST match self-host (main.py:_KARAOKE_STEPS) so the
# frontend's 4-step bar (แยกเสียง→ถอดเนื้อ→จับเวลา→สร้างไฟล์) renders identically.
_STEPS = {"queued": 0, "separating": 1, "transcribing": 2, "aligning": 3,
          "building": 4, "done": 4}


@app.function(image=image, gpu=["T4", "L4", "any"],
              volumes={"/models": models_vol}, secrets=[config],
              region="ap",            # broad Asia-Pacific (1.5x); owner-approved
              timeout=900, max_containers=1)  # one job at a time = VRAM invariant + cost cap
def process_song(song_bytes: bytes, lang: str, job_id: str, filename: str) -> dict:
    """Run the full pipeline on the GPU; return the small JSON payload.

    Stems (m4a bytes) are stashed in the `stems` Dict here, not returned, so the
    web container's poll stays cheap. Temp dir is always deleted (finally).
    """
    import logging
    import os
    import shutil
    import tempfile
    import time
    from pathlib import Path

    from app import asr, main, separate, render

    logger = logging.getLogger("lyricbridge")
    ttl = int(os.getenv("STEM_TTL_SEC", "1800"))
    tmp = Path(tempfile.mkdtemp(prefix="modal_kar_"))
    t0 = time.time()
    try:
        # Preserve the upload's suffix so separate() can demux video (.mp4 etc).
        # separate.separate keys video-vs-audio off src.suffix (separate.py:17);
        # a suffixless temp file would silently skip demux. Mirror self-host's
        # `Path(file.filename).name` (main.py:586).
        src = tmp / (Path(filename or "song").name or "song")
        src.write_bytes(song_bytes)

        progress.put(job_id, {"stage": "separating", "step": 1, "total": 4})
        result = separate.separate(src, tmp)   # OOM→CPU fallback built in
        try:
            duration = main._wav_duration(str(result.vocals_path))
        except Exception:
            duration = 0.0

        resp = main._run_pipeline(
            str(result.vocals_path), lang, duration,
            on_stage=lambda name: progress.put(
                job_id, {"stage": name, "step": _STEPS.get(name, 1), "total": 4}),
        )

        # F1: encode both stems to m4a (small enough for the Dict). Worst case at
        # STEM_BITRATE=128k × MAX_DURATION_SEC=420s ≈ 6.7 MB/stem — well inside
        # Modal Dict's per-value headroom (values are cloudpickle-serialized with
        # no documented small cap; MB-scale bytes are supported). D3 acceptance
        # round-trips a real stem, so a regression here would fail the smoke.
        inst = render.encode_stem(result.instrumental_path, tmp / "inst.m4a").read_bytes()
        voc = render.encode_stem(result.vocals_path, tmp / "voc.m4a").read_bytes()
        exp = time.time() + ttl
        stems.put(f"{job_id}:instrumental", (inst, exp))
        stems.put(f"{job_id}:vocal", (voc, exp))
        progress.put(job_id, {"stage": "done", "step": 4, "total": 4})

        # P3: one machine-parseable summary line per job (`modal app logs | grep`).
        _bump_metric("done")
        logger.info("job_summary job_id=%s status=done total_sec=%.1f gpu=%s "
                    "stages=%s aligned=%s degraded=%d/%d words=%d",
                    job_id, time.time() - t0, asr.resolve_device(), resp.timings_sec,
                    resp.aligned, resp.degraded_segment_count, resp.total_segment_count,
                    len(resp.words))
        return {"payload": resp.model_dump()}
    except Exception as exc:
        _bump_metric("error")
        logger.error("job_summary job_id=%s status=error total_sec=%.1f error=%r",
                     job_id, time.time() - t0, exc)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # never persist user audio
        # D5: release this job's queue slot (runs once per job → counter balances
        # web's increment-on-spawn even when the browser stops polling).
        try:
            gauges.put("inflight", max(0, (gauges.get("inflight") or 0) - 1))
        except Exception:
            pass


@app.function(image=image, cpu=2.0, memory=4096, timeout=600)
def render_job(instrumental_bytes: bytes, ass_text: str, font: str | None,
               background_bytes: bytes | None = None) -> bytes:
    """F2 edit-loop render: parked instrumental m4a + edited ASS → mp4 BYTES.

    Runs on CPU (libx264 via render._resolve_vcodec — no nvenc in this image), in
    its OWN container so a ~minute-long ffmpeg burn never blocks the single web
    container's polls/healthz. No GPU = no VRAM contention with process_song, so
    the "never Demucs+Whisper co-resident" invariant is untouched. Reuses
    render.render_video — no burn code copied here. Temp dir always deleted.

    O1: optional background_bytes (a still image) become the video base; the
    render validates them (ffprobe) before ffmpeg sees them.
    """
    import shutil
    import tempfile
    from pathlib import Path

    from app import render

    tmp = Path(tempfile.mkdtemp(prefix="modal_render_"))
    try:
        audio = tmp / "instrumental.m4a"
        audio.write_bytes(instrumental_bytes)
        bg_path = None
        if background_bytes:
            # Pick an extension from magic bytes so render's extension+ffprobe
            # validation can run (content is the real check; junk → ffprobe rejects).
            b = background_bytes
            ext = (".jpg" if b[:3] == b"\xff\xd8\xff"
                   else ".png" if b[:8] == b"\x89PNG\r\n\x1a\n"
                   else ".webp" if b[:4] == b"RIFF" and b[8:12] == b"WEBP"
                   else ".png")
            bg_path = tmp / f"bg{ext}"
            bg_path.write_bytes(b)
        result = render.render_video(audio, ass_text, tmp, font=font,
                                     background_image=bg_path)  # CPU libx264
        return Path(result.video_path).read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # never persist user audio


@app.function(image=image, secrets=[config], max_containers=1)
@modal.asgi_app()
def web():
    """Tiny CPU FastAPI: submit → spawn, poll → FunctionCall.get, serve stems.

    NOT server/app/main.py's app (its in-memory stores don't survive the split).
    The response shapes mirror self-host exactly so web/player.js is unchanged.
    """
    import logging
    import os
    import time
    import uuid

    from fastapi import Body, FastAPI, File, Form, Request, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, Response

    logger = logging.getLogger("lyricbridge")
    api = FastAPI(title="LyricBridge (Modal)")
    # CORS is process-static (changing it is a real redeploy concern, not a knob).
    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    if not origins:
        # P4 wants "no * in prod". Empty Secret would silently open every origin,
        # so make the fallback LOUD (one WARNING per container start) instead of a
        # quiet wildcard. The owner sets CORS_ORIGINS in the lyricbridge-config
        # Secret to the GitHub Pages origin; the deploy workflow asserts it's set.
        logger.warning("CORS_ORIGINS is empty — falling back to allow_origins=['*'] "
                       "(NOT for prod). Set CORS_ORIGINS in the lyricbridge-config Secret.")
    api.add_middleware(CORSMiddleware, allow_origins=origins or ["*"],
                       allow_methods=["*"], allow_headers=["*"])

    def cfg(key, default):
        # Per-request knob: control Dict (live) → Secret env → default.
        # Empty control value is treated as "unset" so `set_control --key X`
        # (no --value) safely reverts to the env/default instead of crashing int("").
        v = control.get(key)
        if v is None or v == "":
            v = os.getenv(key, default)
        return v

    def err(status, message, stage):
        return JSONResponse(status_code=status, content={"error": message, "stage": stage})

    def allowed_fonts():
        # Mirror main._allowed_render_fonts WITHOUT importing app.main (which pulls
        # torch/whisper into this tiny CPU container). RENDER_FONTS_EXTRA is set in
        # the same Secret the self-host reads, so the allowlist matches.
        extra = {f.strip() for f in os.getenv("RENDER_FONTS_EXTRA", "").split(",") if f.strip()}
        return {"Sarabun", "Noto Sans Thai"} | extra

    def renew_stem_ttl(job_id: str):
        # Self-host renews a job's stem TTL on every access (main._touch_job) so a
        # user editing lyrics past STEM_TTL_SEC can still fetch/re-render. The split
        # backend lost that; restore it here. Both stems share one expiry.
        exp = time.time() + int(cfg("STEM_TTL_SEC", "1800"))
        for kind in ("instrumental", "vocal"):
            e = stems.get(f"{job_id}:{kind}")
            if e is not None:
                stems.put(f"{job_id}:{kind}", (e[0], exp))

    def accepting():
        # $30 kill switch. Owner flips it live: `modal dict put lyricbridge-control
        # ACCEPTING_JOBS 0` (no redeploy). Default = accepting.
        return str(cfg("ACCEPTING_JOBS", "1")).strip().lower() not in ("0", "false", "no", "")

    def client_ip(request: Request):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def probe_audio(data: bytes, filename: str):
        """ffprobe the bytes: (ok, duration_sec, why). Rejects non-audio fast,
        before spending the GPU. ffprobe ships in the image (apt ffmpeg)."""
        import json
        import subprocess
        import tempfile
        from pathlib import Path

        suffix = Path(filename or "").suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix) as f:
            f.write(data)
            f.flush()
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration:stream=codec_type", "-of", "json", f.name],
                capture_output=True, text=True)
        try:
            info = json.loads(r.stdout or "{}")
        except Exception:
            return False, 0.0, "ไฟล์เสียงไม่ถูกต้อง อ่านไม่ได้ (invalid audio file)"
        if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
            return False, 0.0, "ไม่พบสตรีมเสียงในไฟล์ (no audio stream found)"
        dur = float((info.get("format") or {}).get("duration") or 0.0)
        return True, dur, ""

    @api.get("/healthz")
    def healthz():
        # `accepting` drives the frontend "demo paused" banner (player.js).
        # git_sha comes from the control Dict (P1 deploy writes it via set_control
        # → live, no warm-container staleness) so CI's smoke can verify the sha.
        return {"status": "ok", "git_sha": cfg("GIT_SHA", "unknown"),
                "accepting": accepting()}

    @api.post("/jobs/karaoke")
    async def submit(request: Request, file: UploadFile = File(...), lang: str = Form("th")):
        if not accepting():
            return err(503, "เดโมปิดชั่วคราว — เต็มโควต้าเดือนนี้ ลองใหม่เดือนหน้า "
                            "หรือ self-host (demo paused: monthly quota reached)", "queue")
        max_mb = int(cfg("MAX_UPLOAD_MB", "30"))
        max_sec = int(cfg("MAX_DURATION_SEC", "420"))        # 7 min
        rate_per_hour = int(cfg("RATE_LIMIT_PER_HOUR", "5"))  # jobs/IP/hour
        max_queued = int(cfg("MAX_QUEUED", "3"))             # in-flight cap
        # per-IP hourly rate limit
        hour = int(time.time() // 3600)
        rk = f"{client_ip(request)}:{hour}"
        used = ratelimit.get(rk) or 0
        if used >= rate_per_hour:
            return err(429, f"ขอเกินโควตา {rate_per_hour} เพลง/ชม. ต่อผู้ใช้ ลองใหม่ภายหลัง "
                            f"(rate limit {rate_per_hour}/hour)", "queue")
        # queue cap (max_containers=1 already serializes; this caps the backlog)
        if (gauges.get("inflight") or 0) >= max_queued:
            return err(429, "คิวเต็ม กำลังประมวลผลอยู่ ลองใหม่อีกสักครู่ (queue full)", "queue")
        data = await file.read()
        if len(data) > max_mb * 1024 * 1024:
            return err(413, f"upload exceeds {max_mb} MB", "separate")
        # real-file + duration check BEFORE spending the GPU
        ok, dur, why = probe_audio(data, file.filename or "")
        if not ok:
            return err(400, why, "separate")
        if dur > max_sec:
            return err(413, f"เพลงยาวเกิน {max_sec // 60} นาที ({dur:.0f}s > {max_sec}s)", "separate")

        job_id = uuid.uuid4().hex
        call = process_song.spawn(data, lang, job_id, file.filename or "song")
        calls.put(job_id, call.object_id)
        ratelimit.put(rk, used + 1)
        gauges.put("inflight", (gauges.get("inflight") or 0) + 1)
        _bump_metric("submitted")
        return JSONResponse(status_code=202,
                            content={"job_id": job_id, "status_url": f"/jobs/{job_id}"})

    @api.get("/metrics-lite")
    def metrics_lite():
        # P3: not secret, not Prometheus — "how busy / how healthy today" at a glance.
        import datetime

        day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        return {"day": day, **(metrics.get(day) or {"submitted": 0, "done": 0, "error": 0}),
                "in_flight": gauges.get("inflight") or 0}

    @api.get("/jobs/{job_id}")
    def poll(job_id: str):
        call_id = calls.get(job_id)
        if call_id is None:
            return err(404, "job not found or expired", "queue")
        p = progress.get(job_id) or {"stage": "queued", "step": 0, "total": 4}
        fc = modal.FunctionCall.from_id(call_id)
        try:
            out = fc.get(timeout=0)            # TimeoutError if still running
        except TimeoutError:
            return JSONResponse(content={"status": "running", "result": None,
                                         "error": None, **p})
        except Exception as e:                 # the GPU function raised
            return JSONResponse(content={"status": "error", "result": None,
                                         "error": {"error": str(e), "stage": "separate"},
                                         **p})
        # done — build the SAME payload shape self-host returns (main.py:710).
        payload = {**out["payload"], "job_id": job_id,
                   "instrumental_url": f"/instrumental/{job_id}",
                   "vocal_url": f"/vocal/{job_id}"}
        return JSONResponse(content={"status": "done", "result": payload, "error": None,
                                     "stage": "done", "step": 4, "total": 4})

    def _serve_stem(job_id: str, kind: str):
        entry = stems.get(f"{job_id}:{kind}")
        if entry is None:
            return err(404, f"{kind} not found or expired", kind)
        data, exp = entry
        if time.time() > exp:
            return err(404, "expired", kind)
        renew_stem_ttl(job_id)   # accessing a stem keeps the whole job alive
        return Response(content=data, media_type="audio/mp4")

    @api.get("/instrumental/{job_id}")
    def instrumental(job_id: str):
        return _serve_stem(job_id, "instrumental")

    @api.get("/vocal/{job_id}")
    def vocal(job_id: str):
        return _serve_stem(job_id, "vocal")

    @api.post("/render/{job_id}")
    def render_from_job(job_id: str, payload: dict = Body(...)):
        """F2 edit-loop: re-render the karaoke mp4 from the parked instrumental +
        the player's corrected lyrics — no audio re-upload. Same body shape and
        validation as self-host main.render_from_job so web/player.js is unchanged;
        the actual ffmpeg burn runs in the separate render_job container.
        """
        from app.lrc import AssStyle, to_ass, to_lines
        from app.schemas import Word

        lines_in = payload.get("lines")
        words_in = payload.get("words")
        try:
            if lines_in:
                groups = [[Word(**w) for w in ln] for ln in lines_in if ln]
            elif words_in:
                groups = [[Word(**w) for w in words_in]]   # one group; lrc re-breaks
            else:
                groups = []
        except Exception as e:  # malformed word dicts → 400
            return err(400, f"bad word payload: {e}", "render")
        flat = [w for g in groups for w in g]
        if not flat:
            return err(400, "no words to render", "render")

        # F8 optional style fields (server builds the ASS, so style applies here).
        style_kwargs = {
            k: payload[k]
            for k in ("font", "font_size", "primary_colour", "highlight_colour",
                      "alignment", "margin_v")
            if payload.get(k) is not None
        }
        font_override = style_kwargs.get("font")
        if font_override is not None and font_override not in allowed_fonts():
            return err(400, f"font {font_override!r} not in allowlist "
                            "(set RENDER_FONTS_EXTRA to add fonts)", "render")
        try:
            for k in ("font_size", "alignment", "margin_v"):
                if k in style_kwargs:
                    style_kwargs[k] = int(style_kwargs[k])
            style = AssStyle(**style_kwargs)
        except (TypeError, ValueError) as e:
            return err(400, f"bad style: {e}", "render")

        entry = stems.get(f"{job_id}:instrumental")
        if entry is None:
            return err(404, "job not found or expired", "render")
        data, exp = entry
        if time.time() > exp:
            return err(404, "job not found or expired", "render")
        renew_stem_ttl(job_id)   # editing sessions easily outlive the TTL

        try:
            ass_text = to_ass(to_lines(flat, groups), style)
        except Exception as e:
            return err(500, f"ass build failed: {e}", "render")

        # O1: optional background image (base64, optionally data-URL) → bytes.
        # render_job sniffs the type + the render validates via ffprobe.
        bg_bytes = None
        raw_bg = payload.get("background")
        if raw_bg:
            import base64
            if not isinstance(raw_bg, str):
                return err(400, "background must be a base64 string", "render")
            if raw_bg.startswith("data:"):
                raw_bg = raw_bg.partition(",")[2]
            try:
                bg_bytes = base64.b64decode(raw_bg, validate=False)
            except Exception as e:  # noqa: BLE001
                return err(400, f"bad background image encoding: {e}", "render")
            max_bg = int(cfg("MAX_BG_IMAGE_MB", "8")) * 1024 * 1024
            if not bg_bytes:
                return err(400, "empty background image", "render")
            if len(bg_bytes) > max_bg:
                return err(400, f"background image exceeds {max_bg // (1024*1024)} MB", "render")
        try:
            # .remote() blocks this (threadpool) request until the mp4 is ready,
            # without occupying the web event loop. render_job is its own container.
            mp4 = render_job.remote(data, ass_text, font_override, bg_bytes)
        except Exception as e:
            return err(500, f"render failed: {e}", "render")
        return Response(content=mp4, media_type="video/mp4",
                        headers={"Content-Disposition": 'inline; filename="karaoke.mp4"'})

    return api


@app.function(image=image, gpu=["T4", "L4", "any"], volumes={"/models": models_vol},
              secrets=[config], region="ap", schedule=modal.Cron("0 1 * * *"), timeout=600)
def canary():
    """P3 daily health check: full pipeline on a short SYNTHETIC clip — copyright-safe,
    generated here (never bundle the gitignored samples). Raises on failure so a broken
    backend shows as ERROR in `modal app logs`. A tone yields ~0 words; that's fine —
    this validates the *infra* (ffmpeg + demucs + ASR + aligner load and run on GPU)."""
    import logging
    import shutil
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    from app import align, asr, render, separate

    logger = logging.getLogger("lyricbridge")
    tmp = Path(tempfile.mkdtemp(prefix="canary_"))
    try:
        # Validate the fragile infra on a synthetic tone (copyright-safe): ffmpeg +
        # demucs separation + ASR weights + aligner weights all load and run on GPU.
        # We deliberately DON'T call _run_pipeline — a tone has no speech, so ASR would
        # (correctly) reject it; model-load + separation is the real infra coverage.
        src = tmp / "canary.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=12", "-ac", "2", "-ar", "44100",
                        str(src)], check=True, capture_output=True)
        t0 = time.time()
        result = separate.separate(src, tmp)            # ffmpeg + demucs + GPU
        render.encode_stem(result.instrumental_path, tmp / "inst.m4a")  # F1 encode path
        asr._load_model(); asr.free_model()             # Thai CT2 loads from Volume on GPU
        dev = asr.resolve_device()
        align._load_align_model("th", dev); align.free_model()  # wav2vec2 aligner loads
        logger.info("canary OK total_sec=%.1f device=%s", time.time() - t0, dev)
        return "ok"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.function(image=image)
def set_control(key: str, value: str = ""):
    """Owner kill-switch / live knob (no redeploy — web reads control per request):

        modal run deploy/modal_app.py::set_control --key ACCEPTING_JOBS --value 0   # pause
        modal run deploy/modal_app.py::set_control --key ACCEPTING_JOBS --value 1   # resume
        modal run deploy/modal_app.py::set_control --key MAX_QUEUED --value 0       # drain

    Knobs: ACCEPTING_JOBS, MAX_UPLOAD_MB, MAX_DURATION_SEC, RATE_LIMIT_PER_HOUR,
    MAX_QUEUED. Inspect with `modal dict items lyricbridge-control`.
    (The CLI has no `dict put`, so this function is how a value gets written.)
    """
    control[key] = value
    print(f"control[{key!r}] = {value!r}")
    return {key: value}


@app.function(image=image, schedule=modal.Period(minutes=30))
def sweep_stems():
    """Drop expired stem entries (TTL) + the per-job calls/progress that ride along
    with them, prune stale rate-limit buckets, and reconcile the in-flight gauge.

    Runs every 30 min to match STEM_TTL_SEC so the UI privacy claim ("purged within
    ~30 min") holds for physical deletion, not just accessibility. 'Don't persist
    user audio.'
    """
    import time

    now = time.time()
    # 1) Expire stems; remember which job_ids went so their calls/progress (1 entry
    #    each, never otherwise deleted → unbounded growth across deploys) go too.
    expired_jobs = set()
    for k in list(stems.keys()):
        v = stems.get(k)
        if isinstance(v, tuple) and len(v) == 2 and now > v[1]:
            stems.pop(k)
            expired_jobs.add(str(k).rsplit(":", 1)[0])
    for job_id in expired_jobs:
        for d in (calls, progress):
            try:
                d.pop(job_id)
            except KeyError:
                pass

    # 2) Rate-limit keys are "<ip>:<hour>" — drop anything older than the current hour.
    cur_hour = int(now // 3600)
    for k in list(ratelimit.keys()):
        try:
            if int(str(k).rsplit(":", 1)[1]) < cur_hour:
                ratelimit.pop(k)
        except Exception:
            pass

    # 3) Reconcile the in-flight gauge from REALITY. web's increment/process_song's
    #    finally-decrement are a non-atomic get-modify-put pair, and a GPU container
    #    that dies before its finally skips the decrement — the counter then ratchets
    #    up until MAX_QUEUED rejects every submit forever. Recompute from live call
    #    state so any drift self-heals within one sweep (≤30 min).
    running = 0
    for job_id in list(calls.keys()):
        call_id = calls.get(job_id)
        if call_id is None:
            continue
        try:
            modal.FunctionCall.from_id(call_id).get(timeout=0)
        except TimeoutError:
            running += 1          # still queued/executing
        except Exception:
            pass                  # finished (done or errored) → not in flight
    gauges.put("inflight", running)
