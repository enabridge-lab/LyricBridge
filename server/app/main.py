"""FastAPI app + routes — M0 ASR service plus M1 separation prototype.

Pipeline per request (stateless, temp dir, never persist user audio):
    vocal.wav -> [1] ASR -> [2] align -> [3] tokenize+map -> [4] build LRC/ASS

Stages run sequentially and free their model between steps so Demucs (M1) and
Whisper never share the 4 GB GTX 1650 (PRD section 5.1).
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from . import __version__, align, asr, lrc, render, separate, thai
from .lrc import to_ass, to_lines, to_lrc
from .schemas import (
    HealthResponse,
    TranscribeResponse,
    VersionResponse,
    Word,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("lyricbridge")

app = FastAPI(title="LyricBridge — ASR + Separation", version=__version__)

# The frontend is a separate static origin (web on :8080, API on :8000), so the
# browser needs CORS to call /separate, /transcribe, /render. Default allows all
# origins (self-host convenience); set CORS_ORIGINS=comma,sep,list to restrict.
_cors = os.getenv("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors.strip() == "*" else [o.strip() for o in _cors.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


class _LoggingMiddleware:
    """Pure-ASGI request logger — avoids BaseHTTPMiddleware's known issue where
    a FileResponse BackgroundTask can fire before the body is fully streamed,
    causing Chrome to get ERR_FAILED 200 (OK) on large file downloads."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method = scope.get("method", "?")
        path = scope.get("path", "?")
        headers = dict(scope.get("headers", []))
        cl = headers.get(b"content-length", b"-").decode()
        origin = headers.get(b"origin", b"-").decode()
        ua = headers.get(b"user-agent", b"-").decode()[:60]
        logger.info("→ %s %s  origin=%s  content-length=%s  ua=%s", method, path, origin, cl, ua)
        t0 = time.perf_counter()
        status_holder = [None]

        async def _send(message):
            if message["type"] == "http.response.start":
                status_holder[0] = message.get("status")
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                elapsed = round(time.perf_counter() - t0, 3)
                logger.info("← %s %s  status=%s  %.3fs", method, path, status_holder[0], elapsed)

        await self._app(scope, receive, _send)


app.add_middleware(_LoggingMiddleware)


# Heavy endpoints are sync `def` so FastAPI runs them in a threadpool -> the
# event loop stays free and /healthz keeps answering during a ~20 min separate
# (else Docker's healthcheck marks the container unhealthy and restarts it
# mid-job). This lock then serializes the actual inference: only one heavy job
# at a time, honouring the PRD 5.1 "never co-resident on the 4 GB GPU" invariant
# and making separate.py's CUDA_VISIBLE_DEVICES toggle race-free.
_inference_lock = threading.Lock()

# Reject oversized uploads early (public deploys could otherwise fill the disk).
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))

# Include per-stage wall times in /karaoke + /transcribe responses (perf tuning).
EXPOSE_TIMINGS = os.getenv("EXPOSE_TIMINGS", "1").lower() in ("1", "true", "yes")

# F7: attach a romanized reading to every word (Thai learners). Rule-based,
# ~ms per song; ROMANIZE=0 turns it off if a benchmark ever says otherwise.
ROMANIZE = os.getenv("ROMANIZE", "1").lower() in ("1", "true", "yes")


# F8: fonts a render request may pick. The name is interpolated into an ffmpeg
# filter string, so it's an allowlist (not free text) to rule out injection.
def _allowed_render_fonts() -> set[str]:
    extra = {f.strip() for f in os.getenv("RENDER_FONTS_EXTRA", "").split(",") if f.strip()}
    return {"Sarabun", "Noto Sans Thai"} | extra


# O1: cap the decoded background image so a render body can't blow up memory.
MAX_BG_IMAGE_MB = int(os.getenv("MAX_BG_IMAGE_MB", "8"))


def _stage_background_image(b64: str, tmpdir: str | Path) -> Path:
    """Decode a base64 (optionally data-URL) background image into `tmpdir` and
    return its path. Raises ValueError (-> 400) on junk/oversize; content is
    validated by render.is_valid_background_image downstream (ffprobe)."""
    import base64

    if not isinstance(b64, str):
        raise ValueError("background must be a base64 string")
    ext = ".png"
    if b64.startswith("data:"):
        header, _, b64 = b64.partition(",")
        if "jpeg" in header or "jpg" in header:
            ext = ".jpg"
        elif "webp" in header:
            ext = ".webp"
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"bad background image encoding: {e}") from e
    if not raw:
        raise ValueError("empty background image")
    if len(raw) > MAX_BG_IMAGE_MB * 1024 * 1024:
        raise ValueError(f"background image exceeds {MAX_BG_IMAGE_MB} MB")
    dest = Path(tmpdir) / f"bg{ext}"
    dest.write_bytes(raw)
    return dest


def _err(status: int, message: str, stage: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message, "stage": stage})


def _cleanup_err(tmpdir, status: int, message: str, stage: str) -> JSONResponse:
    """Error response that also removes a temp dir (used by the streaming
    endpoints, where the success path defers cleanup to a BackgroundTask)."""
    shutil.rmtree(tmpdir, ignore_errors=True)
    return _err(status, message, stage)


def _save_upload(upload: UploadFile, dest: str | Path) -> int:
    """Stream an upload to disk, aborting if it exceeds MAX_UPLOAD_MB.

    Raises ValueError (-> 413) past the cap. Returns bytes written.
    """
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise ValueError(f"upload exceeds {MAX_UPLOAD_MB} MB limit")
            fh.write(chunk)
    return written


class PipelineError(Exception):
    """A pipeline stage failed. Carries the HTTP status + stage for _err()."""

    def __init__(self, status: int, message: str, stage: str):
        super().__init__(message)
        self.status = status
        self.message = message
        self.stage = stage


# --- /karaoke instrumental hand-off store ----------------------------------
# The one-upload flow separates ONCE, returns the lyrics JSON immediately, and
# parks the (large) instrumental for a follow-up GET so the JSON stays light.
# Still "stateless": each instrumental is a temp file with an opaque job_id,
# auto-deleted after it streams once AND swept on a TTL. The vocal stem and the
# original upload are never kept.
INSTRUMENTAL_TTL_SEC = int(os.getenv("INSTRUMENTAL_TTL_SEC", "600"))  # 10 min

# F1: compress stems to M4A/AAC before they enter the store (WAV ~35-50 MB ->
# ~3-4 MB). STEM_ENCODE=0 keeps the old raw-WAV behaviour (e.g. no usable ffmpeg).
STEM_ENCODE = os.getenv("STEM_ENCODE", "1").lower() in ("1", "true", "yes")

# Content-Type by stored stem extension (FileResponse needs the real type).
_STEM_MEDIA_TYPES = {".m4a": "audio/mp4", ".wav": "audio/wav"}
_JOBS_DIR = Path(tempfile.mkdtemp(prefix="karaoke_jobs_"))
_jobs_lock = threading.Lock()
_jobs: dict[str, tuple[Path, float]] = {}  # job_id -> (instrumental path, expiry)

# Vocal stems: TTL-only cleanup (no pop-on-take, เพื่อให้ re-fetch ได้)
_VOCAL_DIR = Path(tempfile.mkdtemp(prefix="karaoke_vocals_"))
_vocal_jobs_lock = threading.Lock()
_vocal_jobs: dict[str, tuple[Path, float]] = {}  # job_id -> (vocals_path, expiry)


def _encode_stem_or_wav(src: Path) -> Path:
    """Encode a stem WAV to .m4a next to it; return the encoded path.

    Degrades gracefully: STEM_ENCODE=0 or any ffmpeg failure returns the
    original WAV untouched (logged as WARNING) so the flow never breaks.
    Runs OUTSIDE _inference_lock on purpose — AAC encode is light CPU work
    and never touches the GPU.
    """
    if not STEM_ENCODE:
        return src
    dest = src.with_suffix(".m4a")
    try:
        return render.encode_stem(src, dest)
    except Exception as e:  # noqa: BLE001
        logger.warning("stem encode failed, serving raw WAV: %s", e)
        return src


def _store_instrumental(src: Path, job_id: str | None = None) -> str:
    """Encode + move an instrumental into the job store, return its job_id.

    F4: an async job passes its own pre-generated job_id so progress, stems and
    the job record all share ONE id (mirrors _store_vocal's signature)."""
    job_id = job_id or uuid.uuid4().hex
    dest_dir = _JOBS_DIR / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = _encode_stem_or_wav(src)
    dest = dest_dir / f"instrumental{stem.suffix}"
    shutil.move(str(stem), str(dest))
    with _jobs_lock:
        _jobs[job_id] = (dest, time.time() + INSTRUMENTAL_TTL_SEC)
    return job_id


def _get_instrumental(job_id: str) -> Path | None:
    """Return the instrumental path if it exists and hasn't expired, else None.
    Does NOT pop the entry so the browser can make multiple range requests
    (Chrome uses range GETs when the audio element seeks). TTL sweep handles
    cleanup after INSTRUMENTAL_TTL_SEC."""
    with _jobs_lock:
        entry = _jobs.get(job_id)
    if entry is None:
        return None
    path, expiry = entry
    if time.time() > expiry or not path.exists():
        with _jobs_lock:
            _jobs.pop(job_id, None)
        shutil.rmtree(path.parent, ignore_errors=True)
        return None
    return path


def _store_vocal(job_id: str, src: Path) -> None:
    """Encode + move a vocal stem into the vocal store under the given job_id."""
    dest_dir = _VOCAL_DIR / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = _encode_stem_or_wav(src)
    dest = dest_dir / f"vocals{stem.suffix}"
    shutil.move(str(stem), str(dest))
    with _vocal_jobs_lock:
        _vocal_jobs[job_id] = (dest, time.time() + INSTRUMENTAL_TTL_SEC)


def _get_vocal(job_id: str) -> Path | None:
    """Return the vocal path for job_id if it exists and hasn't expired, else None."""
    with _vocal_jobs_lock:
        entry = _vocal_jobs.get(job_id)
    if entry is None:
        return None
    path, expiry = entry
    if time.time() > expiry or not path.exists():
        shutil.rmtree(path.parent, ignore_errors=True)
        with _vocal_jobs_lock:
            _vocal_jobs.pop(job_id, None)
        return None
    return path


# --- F4: async job queue (submit -> poll, no 20-min blocking request) -------
# No new dependencies (self-host promise): threading + queue + in-memory dict,
# same pattern as _jobs/_progress. ONE worker thread pulls jobs in FIFO order,
# so "one heavy job at a time" holds by construction (and _inference_lock still
# guards against the legacy blocking endpoints running concurrently).
JOB_RESULT_TTL_SEC = int(os.getenv("JOB_RESULT_TTL_SEC", "1800"))  # 30 min
MAX_QUEUED_JOBS = int(os.getenv("MAX_QUEUED_JOBS", "3"))

_async_jobs_lock = threading.Lock()
# job_id -> {status: queued|running|done|error, created, expiry|None,
#            result: dict|None, error: {error, stage}|None}
_async_jobs: dict[str, dict] = {}
_job_queue: "queue.Queue[tuple[str, Path, str, str]]" = queue.Queue()


def _job_worker_loop() -> None:
    """Single FIFO worker: runs each queued /jobs/karaoke job to completion."""
    while True:
        job_id, input_path, tmpdir, lang = _job_queue.get()
        with _async_jobs_lock:
            rec = _async_jobs.get(job_id)
            if rec is None:  # swept while waiting (shouldn't happen pre-TTL)
                shutil.rmtree(tmpdir, ignore_errors=True)
                continue
            rec["status"] = "running"
        try:
            payload = _run_karaoke_job(input_path, tmpdir, lang, pid=job_id, job_id=job_id)
            with _async_jobs_lock:
                rec["status"] = "done"
                rec["result"] = payload
        except PipelineError as e:
            with _async_jobs_lock:
                rec["status"] = "error"
                rec["error"] = {"error": e.message, "stage": e.stage}
        except Exception as e:  # noqa: BLE001 - a worker crash must not kill the queue
            logger.exception("async karaoke job %s failed", job_id)
            with _async_jobs_lock:
                rec["status"] = "error"
                rec["error"] = {"error": str(e), "stage": "separate"}
        finally:
            with _async_jobs_lock:
                rec["expiry"] = time.time() + JOB_RESULT_TTL_SEC
            shutil.rmtree(tmpdir, ignore_errors=True)


threading.Thread(target=_job_worker_loop, daemon=True).start()


# --- /karaoke progress (per-job stage, polled by the browser) ---------------
# /karaoke is one long-blocking request, so the browser can't see what stage the
# server is on. The server publishes its current stage here under a client-given
# opaque progress_id; the browser polls GET /progress/<id> during the wait.
# Tiny in-memory dict, swept on a TTL. Order matters for the step number.
_PROGRESS_TTL_SEC = 1800
_KARAOKE_STEPS = {"queued": 0, "separating": 1, "transcribing": 2, "aligning": 3, "building": 4, "done": 4}
_progress_lock = threading.Lock()
_progress: dict[str, dict] = {}  # progress_id -> {stage, step, total, ts}


def _set_progress(pid: str | None, stage: str) -> None:
    if not pid:
        return
    with _progress_lock:
        _progress[pid] = {
            "stage": stage,
            "step": _KARAOKE_STEPS.get(stage, 0),
            "total": 4,
            "ts": time.time(),
        }


def _get_progress(pid: str) -> dict:
    with _progress_lock:
        return dict(_progress.get(pid) or {})


def _clear_progress(pid: str | None) -> None:
    if not pid:
        return
    with _progress_lock:
        _progress.pop(pid, None)


def _sweep_jobs() -> None:
    now = time.time()
    expired = []
    with _jobs_lock:
        for jid, (path, expiry) in list(_jobs.items()):
            if now > expiry:
                expired.append((jid, path))
                _jobs.pop(jid, None)
    for _, path in expired:
        shutil.rmtree(path.parent, ignore_errors=True)
    # Sweep expired vocal stems
    with _vocal_jobs_lock:
        expired_vocals = [
            (jid, path)
            for jid, (path, expiry) in list(_vocal_jobs.items())
            if now > expiry
        ]
        for jid, _ in expired_vocals:
            _vocal_jobs.pop(jid, None)
    for _, path in expired_vocals:
        shutil.rmtree(path.parent, ignore_errors=True)
    # drop stale progress entries too
    with _progress_lock:
        for pid in [p for p, v in _progress.items() if now - v.get("ts", 0) > _PROGRESS_TTL_SEC]:
            _progress.pop(pid, None)
    # F4: drop finished async-job records past their result TTL
    with _async_jobs_lock:
        for jid in [
            j for j, r in _async_jobs.items()
            if r.get("expiry") is not None and now > r["expiry"]
        ]:
            _async_jobs.pop(jid, None)


def _sweeper_loop() -> None:
    while True:
        time.sleep(60)
        try:
            _sweep_jobs()
        except Exception:  # noqa: BLE001 - a sweep error must not kill the thread
            logger.exception("instrumental sweep failed")


threading.Thread(target=_sweeper_loop, daemon=True).start()


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return os.getenv("GIT_SHA", "unknown")


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(
        status="ok",
        device=asr.current_device(),
        asr_model=asr.ASR_MODEL,
        separation_model=separate.SEPARATION_MODEL,
    )


@app.get("/version", response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(
        app_version=__version__,
        asr_model=asr.ASR_MODEL,
        asr_model_revision=asr.ASR_MODEL_REVISION,
        separation_model=separate.SEPARATION_MODEL,
        # cheap config check -- don't load the ~2.9 GiB aligner just to report
        align_available=align.align_configured(),
        # surfaces the last load failure (if any) so silent degrade is visible
        align_load_error=align.align_load_error(),
        git_sha=_git_sha(),
    )


# NOTE: sync `def` (not async) on purpose — see _inference_lock above. Blocking
# ML inference must not run on the event loop or /healthz stalls.
@app.post("/transcribe")
def transcribe(
    file: UploadFile = File(...),
    lang: str = Form("th"),
    format: str = Form("lrc"),
):
    if format not in ("lrc", "ass", "json"):
        return _err(400, f"unknown format '{format}'", "build")

    # --- stateless temp workspace; cleaned in finally, audio never persisted ---
    tmpdir = tempfile.mkdtemp(prefix="karaoke_")
    wav_path = os.path.join(tmpdir, "vocal.wav")
    try:
        try:
            _save_upload(file, wav_path)
        except ValueError as e:
            return _err(413, str(e), "asr")

        try:
            duration = _wav_duration(wav_path)
        except Exception as e:  # noqa: BLE001
            return _err(400, f"unreadable audio: {e}", "asr")

        # Serialize heavy inference: one job at a time across all endpoints.
        with _inference_lock:
            try:
                resp = _run_pipeline(wav_path, lang, duration)
            except PipelineError as e:
                return _err(e.status, e.message, e.stage)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if format == "ass":
        return JSONResponse(content={**resp.model_dump(), "primary": "ass"})
    return JSONResponse(content=resp.model_dump())


def _run_pipeline(
    wav_path: str, lang: str, duration: float, on_stage=None
) -> TranscribeResponse:
    """ASR -> align -> tokenize -> build LRC/ASS from a vocal wav.

    Call while holding _inference_lock. Raises PipelineError on any stage
    failure (the caller maps it to an HTTP response). Stages run sequentially
    and free their model between steps so Demucs and Whisper never share the
    4 GB GPU (PRD 5.1). Reused by /transcribe and /karaoke (no copy-paste).

    `on_stage(name)` (optional) is called as each stage begins so /karaoke can
    publish live progress ("transcribing" -> "aligning" -> "building").
    """
    timings: dict[str, float] = {}

    def _stage(name):
        if on_stage:
            on_stage(name)

    # [1] ASR -----------------------------------------------------------------
    _stage("transcribing")
    _t = time.perf_counter()
    try:
        segments = asr.transcribe(wav_path, lang=lang)
    except Exception as e:  # noqa: BLE001
        logger.exception("ASR failed")
        raise PipelineError(500, str(e), "asr")
    finally:
        asr.free_model()
        timings["asr"] = round(time.perf_counter() - _t, 2)

    if not segments:
        raise PipelineError(422, "no speech detected in vocal stem", "asr")

    # [2] forced alignment (may degrade to interpolation) ---------------------
    _stage("aligning")
    _t = time.perf_counter()
    try:
        align_result = align.align(wav_path, segments, language_code=lang)
    except Exception as e:  # noqa: BLE001
        logger.warning("align stage error, degrading: %s", e)
        align_result = align.AlignResult(None, len(segments), len(segments))
    finally:
        align.free_model()
        timings["align"] = round(time.perf_counter() - _t, 2)

    char_map = align_result.char_map
    aligned = char_map is not None
    degraded_segments = align_result.degraded_segments
    total_segments = align_result.total_segments

    # [3] tokenize + map timings ----------------------------------------------
    try:
        words: list[Word] = []
        line_groups: list[list[Word]] = []
        for i, seg in enumerate(segments):
            tokens = thai.tokenize(seg.text)
            if not tokens:
                continue
            seg_chars = char_map.get(i) if char_map else None
            seg_words = thai.map_words(tokens, seg.start, seg.end, seg_chars)
            # F3: stamp the segment's ASR confidence onto each of its words.
            # Segment-level on purpose — whisper's tokens and PyThaiNLP's tokens
            # are different word sets, and melisma breaks whole phrases anyway.
            conf = asr.segment_confidence(
                getattr(seg, "avg_logprob", None), getattr(seg, "no_speech_prob", None)
            )
            for w in seg_words:
                if conf is not None:
                    w.confidence = round(conf, 3)
                # F7: romanized reading per word (same single pass as F3).
                if ROMANIZE:
                    w.roman = thai.romanize_word(w.text) or None
            words.extend(seg_words)
            line_groups.append(seg_words)
    except Exception as e:  # noqa: BLE001
        logger.exception("tokenize failed")
        raise PipelineError(500, str(e), "tokenize")

    if not words:
        raise PipelineError(422, "transcript produced no Thai words", "tokenize")

    # [4] build LRC + ASS -----------------------------------------------------
    _stage("building")
    _t = time.perf_counter()
    try:
        lines = to_lines(words, line_groups)
        lrc_text = to_lrc(lines)
        ass_text = to_ass(lines)
    except Exception as e:  # noqa: BLE001
        logger.exception("build failed")
        raise PipelineError(500, str(e), "build")
    timings["build"] = round(time.perf_counter() - _t, 2)

    # Structured per-request diagnostic: alignment mode + per-stage timing (the
    # latter is the perf-tuning lever -- shows which stage eats the wall time).
    logger.info(
        "pipeline done: aligned=%s degraded_segments=%d/%d words=%d "
        "asr_model=%s align_model=%s align_device=%s timing=%s",
        aligned, degraded_segments, total_segments, len(words),
        asr.ASR_MODEL, align.ALIGN_MODEL, align._align_device(), timings,
    )

    return TranscribeResponse(
        language=lang,
        duration_sec=round(duration, 2),
        words=words,
        lrc=lrc_text,
        ass=ass_text,
        aligned=aligned,
        degraded_segment_count=degraded_segments,
        total_segment_count=total_segments,
        timings_sec=timings if EXPOSE_TIMINGS else None,
    )


# sync `def`: heavy separation must run in the threadpool, not the event loop.
@app.post("/separate")
def separate_song(file: UploadFile = File(...)):
    """M1 prototype: full song in -> vocals/instrumental zip out (streamed)."""
    tmpdir = tempfile.mkdtemp(prefix="karaoke_sep_")
    input_path = Path(tmpdir) / (Path(file.filename or "song").name or "song")
    zip_path = Path(tmpdir) / "stems.zip"

    try:
        _save_upload(file, input_path)
    except ValueError as e:
        return _cleanup_err(tmpdir, 413, str(e), "separate")

    try:
        with _inference_lock:
            result = separate.separate(input_path, tmpdir)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(result.vocals_path, "vocals.wav")
            zf.write(result.instrumental_path, "instrumental.wav")
    except Exception as e:  # noqa: BLE001
        logger.exception("separation failed")
        return _cleanup_err(tmpdir, 500, str(e), "separate")

    # Stream the zip from disk (no whole-file read_bytes); delete tmpdir AFTER.
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="stems.zip",
        background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True),
    )


# sync `def`: separation + ASR run in the threadpool, not the event loop.
@app.post("/karaoke")
def karaoke(
    file: UploadFile = File(...),
    lang: str = Form("th"),
    progress_id: str = Form(""),
):
    """One-upload flow: full song -> separate ONCE -> transcribe the vocal stem.

    Returns the lyrics JSON immediately plus a `job_id`/`instrumental_url`; the
    (large) instrumental is fetched separately via GET /instrumental/<job_id>
    so this response stays light. Separation runs exactly once per song.

    If the client sends a `progress_id`, the server publishes its current stage
    under it (poll GET /progress/<progress_id>) so the UI can show what it's doing.
    """
    pid = progress_id or None
    tmpdir = tempfile.mkdtemp(prefix="karaoke_one_")
    input_path = Path(tmpdir) / (Path(file.filename or "song").name or "song")
    try:
        try:
            _save_upload(file, input_path)
        except ValueError as e:
            return _err(413, str(e), "separate")

        try:
            payload = _run_karaoke_job(input_path, tmpdir, lang, pid)
        except PipelineError as e:
            return _err(e.status, e.message, e.stage)
        return JSONResponse(content=payload)
    finally:
        # Remove the upload + vocal stem + work files (instrumental already moved).
        shutil.rmtree(tmpdir, ignore_errors=True)


def _run_karaoke_job(
    input_path: Path, tmpdir: str | Path, lang: str, pid: str | None,
    job_id: str | None = None,
) -> dict:
    """Separate -> transcribe -> park stems; the whole /karaoke job body.

    Shared by the blocking POST /karaoke and the F4 async worker (no
    copy-paste). Raises PipelineError on any failure; the caller maps it to an
    HTTP response or a job-record error. Caller owns tmpdir cleanup.
    `job_id` (optional) forces the stem-store id (async jobs use ONE id for
    progress + stems + job record).
    """
    # "queued" until we hold the lock (another job may be running first).
    _set_progress(pid, "queued")

    # One inference slot for the whole job: separate THEN transcribe, so the
    # vocal stem never co-resides with Demucs and we never separate twice.
    # NOTE (public deploy): this holds _inference_lock for the FULL job --
    # separation alone is ~20 min/song on CPU -- so every other heavy request
    # queues behind it. That's intentional (PRD 5.1 serialisation). For new
    # clients, POST /jobs/karaoke queues the wait instead of blocking the
    # request. /healthz stays responsive throughout (sync `def` threadpool).
    with _inference_lock:
        _set_progress(pid, "separating")
        _t_sep = time.perf_counter()
        try:
            result = separate.separate(input_path, tmpdir)
        except Exception as e:  # noqa: BLE001
            logger.exception("separation failed")
            raise PipelineError(500, str(e), "separate")
        separate_sec = round(time.perf_counter() - _t_sep, 2)
        logger.info(
            "separate done: %.1fs model=%s device=%s",
            separate_sec, separate.SEPARATION_MODEL, result.device,
        )

        try:
            duration = _wav_duration(str(result.vocals_path))
        except Exception:  # noqa: BLE001
            duration = 0.0

        resp = _run_pipeline(
            str(result.vocals_path), lang, duration,
            on_stage=lambda name: _set_progress(pid, name),
        )

    # Fold the separate time into the response's per-stage timings.
    if EXPOSE_TIMINGS and resp.timings_sec is not None:
        resp.timings_sec = {"separate": separate_sec, **resp.timings_sec}

    # Park the instrumental for the follow-up GET (moved OUT of tmpdir so the
    # caller's cleanup doesn't take it).
    job_id = _store_instrumental(result.instrumental_path, job_id)

    # Store vocal stem for the guide feature.
    try:
        _store_vocal(job_id, result.vocals_path)
    except Exception:
        logger.warning("could not store vocal stem for job %s", job_id)

    _set_progress(pid, "done")

    payload = resp.model_dump()
    payload["job_id"] = job_id
    payload["instrumental_url"] = f"/instrumental/{job_id}"
    if _get_vocal(job_id) is not None:
        payload["vocal_url"] = f"/vocal/{job_id}"
    return payload


# sync `def` is fine: this only saves the upload then returns 202 — the heavy
# work happens on the worker thread.
@app.post("/jobs/karaoke")
def submit_karaoke_job(file: UploadFile = File(...), lang: str = Form("th")):
    """F4: submit a karaoke job; returns 202 immediately (poll GET /jobs/{id}).

    Same multipart contract as POST /karaoke, but the request doesn't block for
    the ~minutes-long pipeline. One job_id covers the job record, progress and
    the parked stems. The legacy blocking /karaoke stays for old clients.
    """
    with _async_jobs_lock:
        queued = sum(1 for r in _async_jobs.values() if r["status"] == "queued")
    if queued >= MAX_QUEUED_JOBS:
        return _err(429, f"queue full ({MAX_QUEUED_JOBS} jobs waiting); try again later", "queue")

    job_id = uuid.uuid4().hex
    tmpdir = tempfile.mkdtemp(prefix="karaoke_job_")
    input_path = Path(tmpdir) / (Path(file.filename or "song").name or "song")
    try:
        _save_upload(file, input_path)
    except ValueError as e:
        return _cleanup_err(tmpdir, 413, str(e), "separate")

    with _async_jobs_lock:
        _async_jobs[job_id] = {
            "status": "queued", "created": time.time(), "expiry": None,
            "result": None, "error": None,
        }
    _set_progress(job_id, "queued")
    _job_queue.put((job_id, input_path, tmpdir, lang))
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status_url": f"/jobs/{job_id}"},
    )


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    """F4: status of an async karaoke job (browser polls this until done).

    Folds in the stage info /progress tracks, so one poll endpoint covers both
    queue state and pipeline stage. `result` is the exact /karaoke payload."""
    with _async_jobs_lock:
        rec = _async_jobs.get(job_id)
        if rec is None:
            return _err(404, "job not found or expired", "queue")
        body = {
            "status": rec["status"],
            "result": rec["result"],
            "error": rec["error"],
        }
        if rec["status"] == "queued":
            # Position 1 = next up (count of queued jobs submitted before it).
            body["queue_position"] = 1 + sum(
                1 for r in _async_jobs.values()
                if r["status"] == "queued" and r["created"] < rec["created"]
            )
    p = _get_progress(job_id)
    body["stage"] = p.get("stage", "queued")
    body["step"] = p.get("step", 0)
    body["total"] = p.get("total", 4)
    return JSONResponse(content=body)


@app.get("/progress/{progress_id}")
def get_progress(progress_id: str):
    """Current stage of a /karaoke job (polled by the browser during the wait)."""
    p = _get_progress(progress_id)
    return JSONResponse(content=p or {"stage": "unknown", "step": 0, "total": 4})


def _touch_job(job_id: str) -> None:
    """Reset the TTL of a job in BOTH stem stores (they're separate dicts with
    separate locks). Called on every job access so a user who sits editing
    lyrics for >10 min can still fetch stems / re-render without a 404."""
    expiry = time.time() + INSTRUMENTAL_TTL_SEC
    with _jobs_lock:
        entry = _jobs.get(job_id)
        if entry is not None:
            _jobs[job_id] = (entry[0], expiry)
    with _vocal_jobs_lock:
        entry = _vocal_jobs.get(job_id)
        if entry is not None:
            _vocal_jobs[job_id] = (entry[0], expiry)


def _stem_response(path: Path, stem_name: str) -> FileResponse:
    """FileResponse for a stored stem: real media type + range-request support.
    F1 shrank stems to ~3-4 MB m4a, which also removed the keep-alive race that
    once caused Chrome ERR_FAILED 200 on 35 MB WAV bodies. (Plan B if that ever
    resurfaces: read the small file into RAM and serve a plain Response.)"""
    return FileResponse(
        path,
        media_type=_STEM_MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
        filename=f"{stem_name}{path.suffix}",
        content_disposition_type="inline",
    )


@app.get("/instrumental/{job_id}")
def get_instrumental(job_id: str):
    """Serve a /karaoke job's instrumental (TTL-bounded, re-fetchable)."""
    path = _get_instrumental(job_id)
    if path is None:
        return _err(404, "instrumental not found or expired", "instrumental")
    _touch_job(job_id)
    return _stem_response(path, "instrumental")


@app.get("/vocal/{job_id}")
def get_vocal(job_id: str):
    """Serve a /karaoke job's vocal stem (TTL-bounded, re-fetchable)."""
    path = _get_vocal(job_id)
    if path is None:
        return _err(404, "vocal not found or expired", "vocal")
    _touch_job(job_id)
    return _stem_response(path, "vocals")


# sync `def`: ffmpeg burn must run in the threadpool, not the event loop.
@app.post("/render")
def render_song(
    file: UploadFile = File(...),
    ass: str = Form(...),
):
    """M3: instrumental audio + ASS subtitles -> burned karaoke mp4 (streamed).

    `ass` is the subtitle text (e.g. the `ass` field from /transcribe). Returns
    an mp4 with the \\k karaoke sweep burned over a solid background + the audio.
    """
    if not ass.strip():
        return _err(400, "empty ass subtitles", "render")

    tmpdir = tempfile.mkdtemp(prefix="karaoke_render_")
    audio_path = Path(tmpdir) / (Path(file.filename or "audio").name or "audio")

    try:
        _save_upload(file, audio_path)
    except ValueError as e:
        return _cleanup_err(tmpdir, 413, str(e), "render")

    try:
        with _inference_lock:
            result = render.render_video(audio_path, ass, tmpdir)
    except Exception as e:  # noqa: BLE001
        logger.exception("render failed")
        return _cleanup_err(tmpdir, 500, str(e), "render")

    return FileResponse(
        result.video_path,
        media_type="video/mp4",
        filename="karaoke.mp4",
        background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True),
    )


# sync `def`: ffmpeg burn must run in the threadpool, not the event loop.
@app.post("/render/{job_id}")
def render_from_job(job_id: str, payload: dict = Body(...)):
    """F2: re-render the karaoke video from a parked /karaoke instrumental.

    Closes the edit loop: the player POSTs its corrected lyrics here and gets a
    fresh mp4 — no re-upload of audio. Body (JSON), one of:
        {"lines": [[{text,start,end}, ...], ...]}   # preferred: player's lines
        {"words": [{text,start,end}, ...]}          # flat; server re-breaks lines
    The ASS is built server-side from the edited words (reuse lrc.to_lines +
    to_ass — no client-side ASS serializer). Words are NOT re-tokenized: what
    the user edited is already the token stream.

    Body is a plain dict (not a Pydantic model) so validation failures return
    the repo's {error, stage} shape via _err(400, ...) instead of FastAPI's 422.
    """
    lines_in = payload.get("lines")
    words_in = payload.get("words")
    try:
        if lines_in:
            groups = [[Word(**w) for w in ln] for ln in lines_in if ln]
        elif words_in:
            # One outer group: lrc._split_words re-breaks it on gaps/width.
            groups = [[Word(**w) for w in words_in]]
        else:
            groups = []
    except Exception as e:  # noqa: BLE001 - malformed word dicts -> 400
        return _err(400, f"bad word payload: {e}", "render")
    flat = [w for g in groups for w in g]
    if not flat:
        return _err(400, "no words to render", "render")

    # F8: optional style fields (only this endpoint — the server builds the ASS
    # here; the legacy /render takes a finished ASS where style can't apply).
    style_kwargs = {
        k: payload[k]
        for k in ("font", "font_size", "primary_colour", "highlight_colour",
                  "alignment", "margin_v")
        if payload.get(k) is not None
    }
    font_override = style_kwargs.get("font")
    if font_override is not None and font_override not in _allowed_render_fonts():
        return _err(
            400,
            f"font {font_override!r} not in allowlist (set RENDER_FONTS_EXTRA to add fonts)",
            "render",
        )
    try:
        for k in ("font_size", "alignment", "margin_v"):
            if k in style_kwargs:
                style_kwargs[k] = int(style_kwargs[k])
        style = lrc.AssStyle(**style_kwargs)
    except (TypeError, ValueError) as e:
        return _err(400, f"bad style: {e}", "render")

    instrumental = _get_instrumental(job_id)
    if instrumental is None:
        return _err(404, "job not found or expired", "render")
    # Editing sessions easily outlive the 10-min TTL; renew on use.
    _touch_job(job_id)

    try:
        ass_text = to_ass(to_lines(flat, groups), style)
    except Exception as e:  # noqa: BLE001
        logger.exception("ass build failed")
        return _err(500, str(e), "render")

    tmpdir = tempfile.mkdtemp(prefix="karaoke_rerender_")
    # O1: optional background image (base64 in the body). Decode → stage → the
    # render validates it (ffprobe) before ffmpeg sees it. Reject junk as 400.
    bg_path = None
    bg_b64 = payload.get("background")
    if bg_b64:
        try:
            bg_path = _stage_background_image(bg_b64, tmpdir)
        except ValueError as e:
            return _cleanup_err(tmpdir, 400, str(e), "render")
    try:
        # ffmpeg may touch the GPU (NVENC) -> same lock as /render.
        with _inference_lock:
            result = render.render_video(
                instrumental, ass_text, tmpdir, font=font_override,
                background_image=bg_path,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("render failed")
        return _cleanup_err(tmpdir, 500, str(e), "render")

    return FileResponse(
        result.video_path,
        media_type="video/mp4",
        filename="karaoke.mp4",
        background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True),
    )


def _wav_duration(path: str) -> float:
    import soundfile as sf

    info = sf.info(path)
    return float(info.frames) / float(info.samplerate)
