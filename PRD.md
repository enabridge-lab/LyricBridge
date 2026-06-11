# LyricBridge — Product Requirements Document (PRD)

> **For Claude Code / contributors.** This is the build spec. Read it top to bottom before writing code.
> เอกสารนี้คือสเปกสำหรับลงมือ build อ่านให้จบก่อนเขียนโค้ด
>
> **Status:** v1.1 — **M0–M4 all implemented** (this PRD is now reconciled to the as-built code). The original build order and locked decisions are unchanged; sections below note "as built" where the implementation added detail.
> **Source brief:** see `docs/PROJECT_BRIEF.md` (Thai handoff doc) — this PRD supersedes it for build details.
> **As-built docs:** end-to-end walkthrough in `docs/PIPELINE.md`; reproducible clone/run in `docs/REPRODUCIBLE_CLONE.md`.

---

## 0. TL;DR (สำหรับคนทำต่อ / for the agent)

- **เริ่มที่ M0 เสมอ (cloud ASR service).** มันคือจุด validate ความเสี่ยงที่สำคัญที่สุด: "เพลงไทย/ลูกทุ่ง auto-transcribe เวิร์กแค่ไหน" รู้เร็ว = เสียเวลาน้อย.
  **Always start at M0.** It de-risks the core unknown (Thai/luk-thung auto transcription) before any UI work.
- **อย่าใช้ Whisper เปล่า** กับไทย — ใช้ Thai-tuned ASR (Typhoon / GigaSpeech 2) + **PyThaiNLP** ตัดคำ + **WhisperX** จับเวลาระดับคำ.
- **แยกเสียง: เริ่ม server-side Demucs ก่อน** ให้ pipeline จบ end-to-end แล้วค่อยดัน on-device (WebGPU) ทีหลัง.
- **ต่อยอดจาก [nomadkaraoke](https://github.com/nomadkaraoke)** อย่าเขียนใหม่ทั้งหมด.
- **License: MIT.** ต้องเครดิต Demucs/UVR ตาม license ของ model.

---

## 1. Goal / เป้าหมาย

สร้าง **LyricBridge แบบ open-source** ที่ใครก็ download ไปรันเองได้.
Build an **open-source LyricBridge** that anyone can self-host.

**Core value props:**
1. **Bring any song** — user uploads their own audio (`.mp4` / `.mp3`). No fixed song library. / อัปเพลงอะไรก็ได้
2. **Remove vocals** → produce a backing track to sing over. / ลบเสียงร้อง → ได้ backing track
3. **Word-level synced lyrics** running with the beat. / เนื้อวิ่งตามจังหวะระดับคำ
4. **Thai / luk-thung first** — this is both the differentiator and the hardest part. / เน้นเพลงไทย/ลูกทุ่ง

---

## 2. Locked decisions / การตัดสินใจที่ fix แล้ว

**ห้ามเปลี่ยนโดยไม่คุยกับเจ้าของโปรเจกต์ / Do not change without owner sign-off.**

| Topic | Decision | Why / Impact |
|---|---|---|
| **Where it runs** | Hybrid — separation **on-device**, transcribe+sync on **cloud** | Heavy work (separation) on user's machine → server cheap + free scaling + privacy (music never leaves device; only vocal stem is uploaded). |
| **Lyrics source** | **Pure ASR** (no online lyric fetch, no forced manual paste) | Most automatic, but **accepts that luk-thung will be imperfect** → must plan a post-edit path (see §6, §10). |
| **Output (MVP)** | (1) Web player + real-time lyric highlight, (2) rendered karaoke video | Both. |
| **License** | MIT | Must credit Demucs/UVR per their model licenses. |
| **Build order** | M0 → M1 → M2 → M3 → M4 | M0 first because it validates the Thai risk earliest. |

---

## 3. Architecture / สถาปัตยกรรม

```
[user machine]                         [cloud]                       [user machine]
upload song → separate (on-device) → ┬─ Instrumental (stays local) ───────────────┐
                                     └─ Vocals ──→ Thai ASR → tokenize+align → LRC/ASS ┘
                                                                                     ↓
                                                            web player + video render
```

**Key points:**
- Cloud receives **only the vocal stem** → ASR runs on clean vocals (more accurate) + better bandwidth/privacy.
- Instrumental never leaves the device. It is re-joined with the timing file at the player.
- Cloud is stateless: vocal `.wav` in → `LRC`/`ASS` out.

> **As-built note:** the diagram is the *target* hybrid. Today separation runs **server-side** (M1, the agreed "server-side first" step) — the same stateless Docker image does separate + transcribe + render, and still persists nothing. On-device (WebGPU) separation remains the future move toward the diagram above; the locked decision (§2) is unchanged.

---

## 4. Repo structure / โครงสร้าง repo (monorepo)

```
lyricbridge/                # (working dir is still ai-karaoke/; repo = LyricBridge)
├── server/                 # FastAPI: vocal wav → LRC/ASS + separation + render
│   ├── app/
│   │   ├── main.py         # FastAPI app + routes + sequential orchestration
│   │   ├── asr.py          # faster-whisper backend (int8/int8_float16, VAD, repeat-guard)
│   │   ├── align.py        # WhisperX wav2vec2 forced alignment (+ graceful degrade)
│   │   ├── thai.py         # PyThaiNLP newmm tokenization → word boundaries
│   │   ├── lrc.py          # build LRC + ASS (\k tags) from word timings
│   │   ├── separate.py     # M1: server-side separation via python-audio-separator
│   │   ├── render.py       # M3: ffmpeg/libass karaoke video (ASS → mp4)
│   │   └── schemas.py      # pydantic request/response models
│   ├── tests/
│   │   ├── samples/        # real luk-thung stems for validation (gitignored audio)
│   │   ├── run_eval.py     # M0 eval harness (LRCLIB ground-truth)
│   │   ├── test_api.py     # API tests
│   │   └── test_pipeline_units.py   # pure-logic unit tests (54 pass)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── README.md
├── web/                    # M2: static player (upload, play, word highlight) + tests
├── models/                 # local model cache (gitignored): whisper-th-…, audio-separator/
├── scripts/                # setup.sh (build venv), run_gpu.sh / stop_gpu.sh (GPU launcher)
├── docs/                   # ARCHITECTURE, PIPELINE, REPRODUCIBLE_CLONE, RUN_*, PERFORMANCE_TUNING, …
├── docker-compose.yml      # CPU self-host entrypoint (web + asr)
├── docker-compose.override.yml.example   # optional local/offline model wiring
├── CLAUDE.md               # agent operating guide (read first)
├── PRD.md                  # this file
└── LICENSE                 # MIT
```

"**Anyone can run it**" = cloud part is a single Docker image (runs on CPU via faster-whisper), frontend is a static site → near-zero GPU hosting cost. `docker compose up` + static build = done. **As built:** separation also runs server-side (M1) rather than only client-side; the self-host promise still holds because the one Docker image runs everything on CPU.

> **Deploy note:** public repo is `github.com/enabridge-lab/LyricBridge`. Big models are not committed — a fresh clone re-fetches them (see `docs/REPRODUCIBLE_CLONE.md`).

---

## 5. Dev setup — owner's machine / เครื่อง dev ของเจ้าของ

**Target machine (where M0/M1 are developed & validated):**
- Ubuntu 24.04.4 LTS
- AMD Ryzen 7 3750H — 8 threads
- 32 GiB RAM
- **NVIDIA GTX 1650 — 4 GB VRAM** ← the binding constraint

### 5.1 VRAM reality (4 GB) — read this before debugging OOM

4 GB is the **lower edge** of usable. Rules for this machine:

- **Do NOT run Demucs and Whisper on the GPU at the same time.** Run stages sequentially; free the model between stages.
- **faster-whisper:** use `compute_type="int8_float16"` (or `int8`). Full `float16` large-v3 may not fit. / fp16 เต็มอาจไม่พอ
- **Demucs (M1):** use `--segment 7` (or lower) to cap chunk size; long songs OOM at default. / เพลงยาว default จะ OOM
- **Fallback ที่ปลอดภัย:** ทุก stage ต้องรันบน **CPU** ได้ (RAM 32 GB เหลือเฟือ) — แค่ช้ากว่า. Always keep a `--device cpu` path working.

### 5.2 Environment bootstrap

```bash
# system deps
sudo apt update && sudo apt install -y ffmpeg python3-venv python3-pip

# verify GPU + CUDA
nvidia-smi                      # confirm GTX 1650, driver loaded
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# project venv
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 5.3 `requirements.txt` (starting point)

```
fastapi
uvicorn[standard]
python-multipart           # file upload
faster-whisper             # CPU/GPU ASR, int8 quantization
whisperx                   # word-level forced alignment (wav2vec2)
pythainlp                  # Thai word tokenization (newmm)
pydantic
soundfile
numpy
audio-separator[cpu]       # M1: HTDemucs / UVR separation (nomadkaraoke, MIT). GPU box uses audio-separator[gpu]
```

> **As built:** M1 separation uses `audio-separator` (not a hand-rolled Demucs wrapper) per the CLAUDE.md "reuse, don't reinvent" rule. M3 video render shells out to system **ffmpeg** (with `libass` + a Thai font) — no extra Python dep. The GPU venv swaps `audio-separator[cpu]` → `[gpu]`; build it with `./scripts/setup.sh --gpu`.
> If CUDA wheels fight the GTX 1650 / driver, fall back to CPU inference first — correctness before speed.

---

## 6. Thai-specific knowledge — DO NOT FORGET / มุมเพลงไทยที่ห้ามลืม

ปัญหา 3 อย่างที่โปรเจกต์ฝรั่งทั่วไป **ไม่ได้แก้ให้** — ต้องทำเอง.
Three problems generic (English) karaoke projects **don't solve** — we must:

1. **No spaces between words / ไม่มีช่องว่างระหว่างคำ.**
   → Must tokenize with **PyThaiNLP** before word-level highlight. Without it you can only highlight per-line, not per-word. / ถ้าไม่ตัดคำ จะ highlight ได้แค่ทีละบรรทัด

2. **Whisper is weak on Thai / Whisper อ่อนกับไทย.**
   → Use a **Thai-tuned ASR**, not vanilla Whisper. **As built:** the model is swappable via `ASR_MODEL` (the code default is `large-v3` for a zero-config boot, but the shipped `docker-compose.yml` defaults to the Thai-tuned `champkrap/whisper-th-large-v3-ct2`, converted from `biodatlab/whisper-th-large-v3-combined`). Pin it to a commit with `ASR_MODEL_REVISION` for reproducible output (see `docs/REPRODUCIBLE_CLONE.md`). Typhoon / GigaSpeech 2 Thai models remain drop-in alternatives via the same env.

3. **Luk-thung singing is much harder than speech / เสียงร้องลูกทุ่งยากกว่าเสียงพูดมาก** (เอื้อน, vibrato, ลากเสียง / melisma, vibrato, sustained notes).
   → Separating first (clean vocal stem) helps, but **pure auto ASR will still drift.** Plan a **post-edit path** for lyrics/timing even though the default flow is auto.

> ⚠️ **Warning to the implementer / คำเตือน:** "Pure ASR" was chosen deliberately. That means the MVP will be **"usable but not exact"** on luk-thung. Don't be surprised — ship a post-edit (correction) UI as a **fast-follow** (M4). / อย่าเซอร์ไพรส์ เตรียมช่องแก้ไขไว้

---

# 7. PHASE M0 — Cloud ASR Service (DETAILED) / เฟส M0 (ละเอียด)

> **This is where you start. Everything below is the contract for M0.**
> **นี่คือจุดเริ่ม ทุกอย่างด้านล่างคือสัญญาของ M0.**

## 7.1 Objective

A stateless FastAPI service: **POST a vocal `.wav` → receive `LRC` (and `ASS`)** with word-level Thai timing.
รับ vocal `.wav` → คืน `LRC`/`ASS` ที่มี timing ระดับคำภาษาไทย.

This is the single fastest way to learn whether the Thai/luk-thung idea works.

## 7.2 Pipeline (inside the service)

```
vocal.wav
  │
  ├─[1] ASR transcribe        → text + segment timings   (faster-whisper, Thai-tuned)
  ├─[2] forced alignment      → word/char-level timings   (WhisperX + wav2vec2-th)
  ├─[3] Thai tokenization     → re-segment into Thai words (PyThaiNLP newmm)
  │      └─ map char-timings → word spans
  └─[4] build LRC + ASS       → LRC lines + ASS \k karaoke tags
```

**Why alignment AND tokenization:** ASR gives you text; WhisperX gives precise time per character/token; PyThaiNLP tells you where Thai *words* actually begin/end (no spaces). Combine: align char timings, then group chars into PyThaiNLP word spans to get accurate per-word start/end. / รวม char-timing เข้ากับขอบเขตคำของ PyThaiNLP → ได้ start/end ต่อคำ

## 7.3 API contract

### `POST /transcribe`
- **Request:** `multipart/form-data`
  - `file`: vocal stem `.wav` (mono or stereo, 16 kHz+)
  - `lang` (optional, default `"th"`)
  - `format` (optional, `"lrc" | "ass" | "json"`, default `"lrc"`)
- **Response 200 (as built):**
  ```json
  {
    "language": "th",
    "duration_sec": 213.4,
    "words": [
      {"text": "ฉัน", "start": 12.31, "end": 12.58},
      {"text": "คิดถึง", "start": 12.58, "end": 13.10}
    ],
    "lrc": "[00:12.31]ฉันคิดถึง...",
    "ass": "Dialogue: ... {\\k27}ฉัน{\\k52}คิดถึง...",
    "aligned": true,
    "degraded_segment_count": 0,
    "total_segment_count": 42,
    "timings_sec": {"asr": 8.1, "align": 5.3, "build": 0.1}
  }
  ```
  `aligned`/`degraded_segment_count`/`total_segment_count` surface how much of the song
  fell back to interpolated timing (the §10 risk-3 degrade); `timings_sec` is per-stage
  wall time (omitted unless `EXPOSE_TIMINGS=1`).
- **Response 4xx/5xx:** `{ "error": "...", "stage": "separate|asr|align|tokenize|build" }`

### `GET /healthz`
- `{ "status": "ok", "device": "cuda|cpu", "asr_model": "...", "separation_model": "..." }`

### `GET /version`
- `{ "app_version", "asr_model", "asr_model_revision", "separation_model", "align_available", "align_load_error", "git_sha" }` — `align_load_error` makes a silent alignment degrade visible.

### Pipeline / media endpoints (as built — M1–M3)
- **`POST /separate`** — full song (`.mp3`/`.mp4`/`.wav`/video) → zip of `vocals.wav` + `instrumental.wav` (M1).
- **`POST /karaoke`** — **one-upload flow:** full song → separate → transcribe in one call; returns the lyrics JSON (as `/transcribe`) plus a `job_id`. Publishes live per-stage progress. *Deprecated for new clients in favour of the async queue below (kept for backward compat).*
- **`POST /jobs/karaoke`** / **`GET /jobs/{job_id}`** — **async queue (as built, F4):** same multipart contract, but returns **202** `{job_id, status_url}` immediately; the browser polls `/jobs/{id}` for `{status, stage, step, result, error, queue_position}`. Single FIFO worker thread + in-memory dict (no new dependencies — self-host promise holds); jobs still run one at a time (VRAM invariant). `MAX_QUEUED_JOBS` (default 3) → 429 when full; finished records swept after `JOB_RESULT_TTL_SEC` (default 1800). The web player stores the in-flight job in `localStorage`, so a page refresh resumes polling instead of losing the run.
- **`GET /progress/{progress_id}`** — current stage of a running `/karaoke` job (browser polls this; step 0–4).
- **`GET /instrumental/{job_id}`** / **`GET /vocal/{job_id}`** — stems parked by `/karaoke` (opaque id, TTL-swept; every access renews the TTL). Served as **M4A/AAC** (~10× smaller than WAV; `STEM_BITRATE`, default `128k`) with range-request support; `STEM_ENCODE=0` or an ffmpeg failure falls back to raw WAV. Note: AAC adds ~20–50 ms encoder delay — the player's sync-offset slider absorbs it.
- **`POST /render`** — instrumental audio + ASS subtitles → burned karaoke `.mp4` (M3).
- **`POST /render/{job_id}`** — re-render from the parked instrumental with **edited** lyrics (`{"lines": [[{text,start,end},…],…]}` from the player). Closes the post-edit loop without re-uploading audio; ASS is rebuilt server-side via `lrc.to_lines`/`to_ass` (words are not re-tokenized — the user's edits are the tokens). **Style (as built, F8):** optional flat fields `font, font_size, primary_colour, highlight_colour, alignment, margin_v` feed `lrc.AssStyle` (defaults reproduce the historic header byte-for-byte; colours are web RRGGBB converted to ASS BGR; fonts restricted to an allowlist + `RENDER_FONTS_EXTRA` because the name lands in an ffmpeg filter string). The legacy `/render` takes finished ASS text, so style fields don't apply there.

> Statelessness holds across all of these: each works in a temp dir and cleans up; only the `/karaoke` instrumental is parked briefly behind an opaque job id. No user audio is persisted.
>
> **Confidence (as built, F3):** every word in a lyrics response carries `confidence` (0..1, segment-level — whisper's `avg_logprob`/`no_speech_prob`, no extra model calls; whisper tokens don't map 1:1 to PyThaiNLP tokens so per-word mapping is deliberately not attempted). The player underlines words below 0.55 in orange to point the post-edit eye at the shaky spots; payloads without the field (pre-F3 exports) load unchanged.
>
> **Word hints & badge (as built, F5–F7):** `Word.interpolated` flags words whose timing was guessed rather than char-aligned (player fades them; user-set times clear the flag), and `Word.roman` carries a PyThaiNLP `royin` romanization for Thai learners (🔤 toggle; `ROMANIZE=0` to disable). The player's `syncQuality()` turns `aligned` + degraded counts into a 🟢/🟡/🔴 badge so the timing expectation is set before singing. All of these are optional-with-default schema fields — old payloads load unchanged — and none of them (confidence/interpolated/roman) are written into edited exports.

## 7.4 Module responsibilities

- **`asr.py`** — load faster-whisper (`int8_float16` on GPU, `int8` on CPU). Expose `transcribe(wav_path) -> segments`. Make the model name configurable via env (`ASR_MODEL`), so we can swap in Typhoon / GigaSpeech 2 Thai without code changes.
- **`align.py`** — WhisperX `load_align_model(language_code="th")` + `align(...)`. Returns char/word timings. Handle the case where the Thai wav2vec2 align model is missing → fall back to segment-level timing and log a warning (this is a **known risk**, see §10).
- **`thai.py`** — `tokenize(text) -> list[str]` via PyThaiNLP `newmm`. Plus `map_words(...) -> word_spans` (greedy char-walk with linear interpolation for unmatched tokens; enforces monotonic, non-overlapping spans).
- **`lrc.py`** — `to_lines(...)`, `to_lrc(lines)`, `to_ass(lines)` (ASS uses `\k` centisecond tags for per-word color sweep; long ASR segments are re-broken into screen-sized lines).
- **`separate.py`** *(M1, as built)* — `separate(input, work_dir)` via `audio-separator`; always yields exactly `vocals.wav` + `instrumental.wav` (sums non-vocal stems for 4-stem Demucs). CUDA-OOM→CPU fallback; self-releases VRAM so it never co-resides with Whisper.
- **`render.py`** *(M3, as built)* — `render_video(audio, ass, work_dir)`; ffmpeg burns the ASS over the instrumental, forcing a Thai font; NVENC when available, else `libx264`.
- **`main.py`** — wire the routes, run stages **sequentially** freeing each model between them (PRD §5.1), do file I/O in a temp dir, never persist user audio. One `_inference_lock` serializes heavy jobs.

## 7.5 Step-by-step build order for M0

1. **Scaffold** `server/` with FastAPI, `/healthz`, Dockerfile, `requirements.txt`. Confirm `uvicorn app.main:app` boots.
2. **ASR only** — implement `asr.py` with faster-whisper (`th`). Endpoint returns raw transcript + segment timings. Test on one luk-thung vocal stem.
3. **Tokenize** — add `thai.py` (PyThaiNLP). Verify Thai text splits into sensible words.
4. **Align** — add `align.py` (WhisperX). Get char/word timings; map into PyThaiNLP word spans.
5. **Build LRC/ASS** — add `lrc.py`. Endpoint returns full response per §7.3.
6. **Validate** — run the 5-song acceptance set (§7.6). Eyeball lyrics + timing. **This is the go/no-go for the whole concept.**
7. **Dockerize** — confirm `docker compose up` runs the service **on CPU** (no GPU assumed for self-hosters).

## 7.6 Acceptance criteria for M0 / เกณฑ์ผ่าน M0

- [x] `POST /transcribe` with a vocal `.wav` returns valid `LRC` + `ASS` + word JSON.
- [x] Word objects have monotonic, non-overlapping `start`/`end` (enforced in `thai.py`).
- [x] Thai text is tokenized at **word** level (PyThaiNLP newmm).
- [x] Runs end-to-end **on CPU** inside Docker (self-host requirement; real CPU e2e PASS).
- [x] Runs on the GTX 1650 with `int8_float16` (GPU path hardened; large CUDA run still to be re-confirmed on the card).
- [x] **M0 owner-signed-off (2026-06-08).** Per-song eval notes in `docs/M0_EVAL_NOTES.md`; a broader 5-song luk-thung re-eval against the current Thai model is still recommended.
- [x] `server/README.md` documents how to run + curl example.

## 7.7 M0 validation harness

Put 5 representative luk-thung vocal stems in `server/tests/samples/`. Write `server/tests/run_eval.py` that POSTs each, dumps `LRC` + word JSON to `tests/out/`, and prints a quick report (word count, total duration, % words with timing). The goal is a **human-readable diff against reality**, not an automatic pass/fail — luk-thung accuracy is a judgment call.

### Ground-truth via LRCLIB / ใช้ LRCLIB เป็น ground-truth

Use **[LRCLIB](https://github.com/tranxuanthang/lrclib)** (free, MIT, synced-lyrics DB at `lrclib.net`) to get **human-made reference lyrics** for the test songs, so the eval produces *numbers*, not just eyeballing.

**API (verified against live `lrclib.net`):**

- **Exact match:** `GET https://lrclib.net/api/get?track_name=...&artist_name=...&album_name=...&duration=<sec>`
  Returns `404` if no exact match. `duration` is in **seconds** and must be close (±2s) or it misses.
- **Fuzzy search (use when exact-get 404s):** `GET https://lrclib.net/api/search?q=...` or `?track_name=...&artist_name=...` → returns an **array** of candidates; pick the best by duration/title.
- **Response fields:**
  ```json
  {
    "id": 9332521,
    "trackName": "...", "artistName": "...", "albumName": "...",
    "duration": 233.0,
    "instrumental": false,
    "plainLyrics": "I feel your breath...\n...",
    "syncedLyrics": "[00:17.10] I feel your breath...\n[00:20.58] A soft caress..."
  }
  ```
- **Set a real `User-Agent`** (app name + version + contact/repo URL) and respect rate limits — this is requested by LRCLIB.

**⚠️ Critical limitation — LRCLIB `syncedLyrics` is LINE-LEVEL, not word-level.** Timestamps are per lyric line (`[mm:ss.xx]`), with no per-word timing. / เป็น timing ระดับบรรทัด ไม่ใช่ระดับคำ

So in `run_eval.py`:
- **Text accuracy:** compare ASR transcript vs LRCLIB `plainLyrics` → report **WER / CER** (CER is fairer for Thai). Good ground truth. ✅
- **Line timing:** compare ASR line starts vs LRCLIB line timestamps → report median/p90 offset. Good ground truth. ✅
- **Word timing:** LRCLIB **cannot** validate this — our word-level highlight (the §6 Thai feature) still needs manual spot-checks. ❗
- This is **dev/eval tooling only — it does NOT touch the product flow,** so it does **not** violate the "pure ASR" locked decision (§2).
- **Coverage caveat:** LRCLIB coverage for **Thai luk-thung is likely thin** — many test songs won't be in the DB. Try `/api/get`, then `/api/search`; if still nothing, fall back to manual judgment. Record per-song whether a reference existed.
- LRCLIB is self-hostable (Docker, Rust+SQLite) for offline/reproducible evals, but the public API is fine for 5 songs.

---

# 8. PHASES M1–M4 — Summary / เฟสที่เหลือ (สรุป)

> **All of M1–M4 are implemented** (✅). Outline + acceptance bar below, annotated with what was actually built.
> M1–M4 สร้างเสร็จแล้ว ด้านล่างคือ outline + เกณฑ์ผ่าน พร้อมหมายเหตุของจริง.

### M1 — Separation / แยกเสียง ✅
- **Server-side separation** closes the end-to-end loop; on-device (WebGPU/ONNX) remains a future option, not built.
- **As built:** via **[`python-audio-separator`](https://github.com/nomadkaraoke/python-audio-separator)** (MIT). Default model is single **`htdemucs.yaml`** (owner-approved switch from the `htdemucs_ft` ensemble — ~4× faster on CPU; compose still pins `htdemucs_ft` for best quality). **UVR-MDX-NET Karaoke 2** available for "keep backing vocals/chorus" via `SEPARATION_MODEL`.
- Extracts audio from video containers with **ffmpeg**. On GTX 1650: `DEMUCS_SEGMENT=7`, sequential with ASR, CUDA-OOM→CPU fallback (§5.1).
- **Pass:** upload song → clean `instrumental` + `vocals` stems. ✅

### M2 — Web player / เล่นในเว็บ ✅
- **As built:** static page (no build step) plays the instrumental and highlights lyrics **word-by-word** in real time, with an offset slider for fine sync; live per-stage progress during `/karaoke`.
- **Pass:** lyrics scroll on-beat in the browser. ✅ (headless-verified)

### M3 — Video render / render วิดีโอ ✅
- **As built:** **ffmpeg** burns **ASS** (`\k` sweep) over the instrumental via `render.py` (`POST /render`); forces a Thai-capable font; NVENC or `libx264`.
- **Pass:** produces a `.mp4` karaoke file. ✅

### M4 — Polish & ship / เก็บงาน + ปล่อย ✅ (post-edit UI = fast-follow)
- **As built:** `docker-compose.yml` (CPU) + `scripts/setup.sh` + `scripts/run_gpu.sh` (GPU host) + `docs/RUN_IT_YOURSELF.md` / `docs/REPRODUCIBLE_CLONE.md`.
- **Post-edit UI** for lyrics/timing — **deferred fast-follow** (basic offset + post-edit hooks exist; full correction UI still pending).
- **Pass:** an outsider can `git clone` → run it themselves. ✅

---

## 9. Reference projects / โปรเจกต์อ้างอิง (study, don't start from scratch)

| Project | What to take |
|---|---|
| **nomadkaraoke** (`karaoke-gen` + `python-audio-separator` + `python-lyrics-transcriber`) | Most mature architecture, MIT. Anchor-sequence + LLM auto-correct of lyrics, export ASS/LRC/CDG/video, review UI. **Primary template.** |
| **OpenKara** (thedavidweng) | Philosophy: turn an existing library into karaoke + on-device separation. |
| **karaok-AI** (EtienneAb3d) | Timing editor for manual correction — **important for Thai post-edit**. |
| **KarAIoke** (dylanbliss) | UX ideas (bouncing ball, generative backgrounds). ⚠️ Avoid scope creep like theirs. |

---

## 10. Risks to validate / ความเสี่ยงที่ต้อง validate

1. **On-device WebGPU separation is the newest/riskiest piece.** Perf/VRAM on phones may not hold. And note: even the owner's GTX 1650 (4 GB) needs care — most users have weaker/no GPUs, which is *more* reason to ship server-side first.
   *Fallback:* desktop runner (bundle Demucs) or temporarily move separation to cloud.
2. **Auto ASR on luk-thung won't be exact.** Set expectations + ship post-edit (M4).
3. **Forced alignment on Thai singing.** May need to find/train a Thai wav2vec2 alignment model. **Test this in M0** — if the Thai align model is weak/missing, `align.py` must degrade gracefully to segment-level timing.
4. **Copyright.** The tools/models (MIT) are fine, but the *songs* belong to the user. Separating stems does **not** change a song's copyright status — state this clearly in `docs/`. / การแยก stem ไม่เปลี่ยนสถานะลิขสิทธิ์เพลง

## 10.1 Open questions — decide AFTER M0 / คำถามค้าง ตัดสินใจหลัง M0

- **LRCLIB as an optional lookup path?** If M0 shows ASR drifts badly on luk-thung, consider adding an **optional** "lookup [LRCLIB](https://github.com/tranxuanthang/lrclib) first, fall back to ASR" path for songs that happen to exist in the DB (human-made LRC is far more accurate, especially timing).
  - ⚠️ **This contradicts the locked "pure ASR — no online lyric fetch" decision (§2)** → requires **owner sign-off** before implementing.
  - If adopted, keep it an **opt-in option, default OFF**, to preserve the "fully auto / no online fetch" stance as the product's default identity. / เก็บเป็น option ปิดไว้ก่อน
  - Reality check: LRCLIB's Thai/luk-thung coverage is likely thin, so this is a "fast path for popular songs," **not a replacement for ASR** — the hard long-tail luk-thung songs are exactly the ones LRCLIB won't have.
  - Decision input: use the §7.7 WER/timing numbers to judge whether the accuracy gain is worth diluting the "auto-only" positioning.

---

## 11. Definition of done — MVP

A self-hoster can: upload a Thai song → get a backing track → see word-synced Thai lyrics in a web player → export a karaoke `.mp4`, all from a `docker compose up` + static frontend, with a documented path to correct imperfect luk-thung lyrics.
