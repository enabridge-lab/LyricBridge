# Run it yourself

LyricBridge turns any song into word-synced Thai karaoke: it **separates** the
vocals, **transcribes + time-aligns** the lyrics, lets you **play** them in the
browser with word-by-word highlight, and **renders** a karaoke `.mp4`.

Everything runs **on CPU** — no GPU required. A GPU just makes it faster.

> ⚠️ **Copyright:** separating stems does **not** change a song's copyright.
> Only process audio you have the right to use. See
> [COPYRIGHT_AND_LICENSES.md](COPYRIGHT_AND_LICENSES.md).

---

## Option A — Docker (easiest)

Needs Docker + Docker Compose.

```bash
git clone <repo> lyricbridge && cd lyricbridge
docker compose up --build
```

- **Player:** http://localhost:8080
- **API:** http://localhost:8000 (`/healthz`, `/separate`, `/transcribe`, `/render`)

First start downloads the ASR + separator models (a few GB); they're cached in
named volumes so later starts are fast. `start_period` gives the healthcheck
120 s for that first download.

### Make a karaoke video end-to-end

```bash
# 1. Separate -> vocals.wav + instrumental.wav
curl -s -X POST http://localhost:8000/separate -F "file=@song.mp3" -o stems.zip
unzip -o stems.zip

# 2. Transcribe the VOCAL stem -> word timings + LRC + ASS (JSON)
curl -s -X POST http://localhost:8000/transcribe \
  -F "file=@vocals.wav" -F "format=json" -o lyrics.json

# 3a. Play in the browser: open http://localhost:8080,
#     load instrumental.wav + lyrics.json. Words light up in time.

# 3b. Or render a karaoke .mp4 (burns the ASS \k sweep over the instrumental):
ASS=$(python -c "import json;print(json.load(open('lyrics.json'))['ass'])")
curl -s -X POST http://localhost:8000/render \
  -F "file=@instrumental.wav" -F "ass=$ASS" -o karaoke.mp4
```

The contract: **only the vocal stem is transcribed** (cleaner lyrics), and the
cloud service is **stateless** — audio is processed in a temp dir and never
persisted.

---

## Option B — Local, no Docker

### Backend (server/)

```bash
cd server
python3 -m venv .venv
# CPU (the --extra-index-url keeps torch CPU-only; without it pip pulls ~2.5 GB CUDA torch):
.venv/bin/pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
# GPU instead: .venv/bin/pip install "audio-separator[gpu]"  (and CUDA torch)

# system deps: ffmpeg + a Thai font for legible video burn-in
sudo apt-get install -y ffmpeg fonts-thai-tlwg

.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Frontend (web/) — static, no build

```bash
python -m http.server 8080 --directory web
# open http://localhost:8080
```

Serve over HTTP (not `file://`) — the player is an ES module.

---

## Configuration (env vars)

| Var | Default | What |
|---|---|---|
| `ASR_MODEL` | `large-v3` | Whisper model. Point at a Thai-tuned CT2 model for better luk-thung text. |
| `ASR_DEVICE` / `SEPARATION_DEVICE` | `cpu` | `cuda` to use a GPU (never run both stages on a 4 GB card at once). |
| `ALIGN_MODEL` | `airesearch/wav2vec2-large-xlsr-53-th` | Thai forced-alignment model. `none` → segment-level timing. |
| `ALIGN_DEVICE` | _(follows ASR device)_ | Force the align stage onto a device, e.g. `cpu`, when the ~2.9 GB aligner OOMs your GPU. Slower but yields **real** word timing instead of silently interpolating. |
| `SEPARATION_MODEL` | `htdemucs.yaml` | Separation model — see speed/quality presets below. |
| `DEMUCS_SEGMENT` | `7` | Lower = less VRAM (PRD §5.1). |
| `ASR_BEAM_SIZE` | `5` | Whisper beam width. `1` (greedy) is faster, slightly less accurate. |
| `RENDER_VCODEC` | `libx264` | `h264_nvenc` to GPU-encode the video (NVENC card). Auto-falls back to libx264 if unavailable. |
| `RENDER_FONT` | `Sarabun` (Docker) | Font for the burned-in subtitles. Must be installed. |
| `EXPOSE_TIMINGS` | `1` | Include per-stage wall times (`timings_sec`) in `/transcribe` & `/karaoke` responses. |
| `LRC_MAX_GAP_SEC` / `LRC_MAX_DUR_SEC` / `LRC_MAX_CHARS` | `0.7` / `7.0` / `30` | Karaoke line-break tuning. |

### Speed — separation is ~95% of the time

On CPU, separation dominates. Pick a model with `SEPARATION_MODEL`:

| Preset | Speed (CPU) | Quality | Notes |
|---|---|---|---|
| `htdemucs.yaml` *(default)* | ~6 min/song | good | Single Demucs model. |
| `htdemucs_ft.yaml` | ~26 min/song | best | 4-model ensemble (what M0 eval used). |
| `UVR_MDXNET_KARA_2.onnx` | fast | good vocals | Native 2-stem; keeps backing vocals/chorus. |

**Fast mode (one line):** `SEPARATION_MODEL=htdemucs.yaml ASR_BEAM_SIZE=1`.

**GPU fast mode** (GTX 1650 etc.) — each stage runs sequentially, never co-resident
(PRD §5.1), and a CUDA OOM auto-retries that stage on CPU:
```
SEPARATION_DEVICE=cuda ASR_DEVICE=cuda ALIGN_DEVICE=cuda RENDER_VCODEC=h264_nvenc
```

---

## Word sync accuracy (if lyrics drift from the audio)

Most "words don't track the singing" issues are the **forced-alignment model
silently failing to load** — then every word falls back to interpolated timing.
Check it directly:

- `GET /version` → `align_load_error` tells you if the aligner failed to load.
- Each `/transcribe` response now carries `aligned` and `degraded_segment_count`
  / `total_segment_count`. If `degraded == total`, alignment isn't working.
- The server logs one line per request: `pipeline done: aligned=… degraded_segments=…`.

Fixes, in order of impact:
1. **Pre-download the align model into the image** so a flaky network at runtime
   can't break it. In the Dockerfile, after installing requirements:
   ```dockerfile
   RUN python -c "import whisperx; whisperx.load_align_model(language_code='th', \
       model_name='airesearch/wav2vec2-large-xlsr-53-th', device='cpu')"
   ```
2. If it's GPU OOM (4 GB card), set `ALIGN_DEVICE=cpu` — slower, but real timing.
3. The player has a **sync offset slider** for a constant lead/lag, and an
   **edit mode** (Space = stamp the current word to the playhead) for hand-fixes.

## Known limits

- **Luk-thung lyrics won't be exact.** Melisma/vibrato drift ASR, and quiet
  passages can hallucinate. A post-edit UI for fixing lyrics/timing is the
  planned fast-follow (PRD M4).
- **On-device (in-browser) separation** is a future step; today separation runs
  server-side.

---

## Tests

```bash
# backend
cd server && .venv/bin/python -m pytest -q
# frontend logic
cd web && node --test
```
