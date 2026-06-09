# server — ASR + Separation Service

Stateless FastAPI service: **POST a vocal `.wav` → get word-level Thai-timed
`LRC` + `ASS` + JSON.** This is the M0 deliverable (see `../PRD.md` §7) and the
go/no-go test for the whole project: *does auto-transcribing Thai/luk-thung
actually work?*

M1 adds a server-side Demucs prototype: **POST a full song → get
`vocals.wav` + `instrumental.wav` in a zip.** This is deliberately a prototype
before the PRD target of on-device/WebGPU separation.

## Pipeline

```
song.mp3/mp4/wav
  └─[M1] separation    (Demucs two-stem vocals/no_vocals)
       ├─ vocals.wav   -> /transcribe
       └─ instrumental.wav -> web player / video render

vocal.wav
  ├─[1] ASR transcribe   (faster-whisper, Thai-tuned via ASR_MODEL)
  ├─[2] forced alignment (WhisperX wav2vec2 → char timings; degrades gracefully)
  ├─[3] Thai tokenize    (PyThaiNLP newmm → real word boundaries, no spaces)
  └─[4] build LRC + ASS  (\k centisecond karaoke tags)
```

## Run locally

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # first run downloads models

# CPU (always works; self-host default):
ASR_DEVICE=cpu uvicorn app.main:app --port 8000

# GPU (GTX 1650 4GB — int8_float16, no OOM):
ASR_DEVICE=cuda SEPARATION_DEVICE=cuda DEMUCS_SEGMENT=7 uvicorn app.main:app --port 8000
```

## Run with Docker (CPU, self-host)

```bash
# from repo root
docker compose up --build
```

## API

### `POST /separate`  (multipart/form-data)
| field | required | notes |
|---|---|---|
| `file` | yes | full song `.mp3`, `.mp4`, `.wav`, etc. readable by ffmpeg/Demucs |

Returns `application/zip` with:
- `vocals.wav`
- `instrumental.wav`

```bash
curl -s -X POST http://localhost:8000/separate \
  -F "file=@song.mp3" -o stems.zip
```

Backed by [`python-audio-separator`](https://github.com/nomadkaraoke/python-audio-separator)
(MIT). 4-stem models (HTDemucs) get their instrumental synthesised by summing the
non-vocal stems; 2-stem models (MDX/Karaoke) pass through.

Environment:
- `SEPARATION_MODEL=htdemucs_ft.yaml` (default) — or `UVR_MDXNET_KARA_2.onnx` to keep backing vocals/chorus
- `SEPARATION_DEVICE=cpu|cuda|auto` (`cpu` is self-host default)
- `DEMUCS_SEGMENT=7` keeps GTX 1650 VRAM pressure low

### `POST /transcribe`  (multipart/form-data)
| field | required | default | notes |
|---|---|---|---|
| `file` | yes | — | vocal stem `.wav` (16 kHz+) |
| `lang` | no | `th` | language code |
| `format` | no | `lrc` | `lrc` \| `ass` \| `json` |

```bash
curl -s -X POST http://localhost:8000/transcribe \
  -F "file=@tests/samples/song1.wav" \
  -F "lang=th" -F "format=json" | jq .
```

Response (200):
```json
{
  "language": "th",
  "duration_sec": 213.4,
  "words": [{"text": "ฉัน", "start": 12.31, "end": 12.58}],
  "lrc": "[00:12.31]ฉันคิดถึง...",
  "ass": "Dialogue: ... {\\k27}ฉัน...",
  "aligned": true
}
```
`aligned: false` means the Thai alignment model was unavailable and timing fell
back to segment-level interpolation (a known, accepted risk — PRD §10).

Errors name the failing stage:
`{ "error": "...", "stage": "separate|asr|align|tokenize|build" }`.

### `POST /render`  (multipart/form-data)
Burns the ASS `\k` karaoke sweep over the instrumental into an mp4 (M3).

| field | required | notes |
|---|---|---|
| `file` | yes | instrumental audio (the `instrumental.wav` from `/separate`) |
| `ass` | yes | ASS subtitle text (the `ass` field from `/transcribe`) |

Returns `video/mp4`.

```bash
ASS=$(curl -s -X POST :8000/transcribe -F "file=@vocals.wav" -F format=json | jq -r .ass)
curl -s -X POST http://localhost:8000/render \
  -F "file=@instrumental.wav" -F "ass=$ASS" -o karaoke.mp4
```

Environment:
- `RENDER_FONT=Sarabun` — must be an installed Thai-capable font (Docker installs `fonts-thai-tlwg`); falls back to `Noto Sans Thai` locally
- `RENDER_WIDTH=1280` `RENDER_HEIGHT=720` `RENDER_FPS=24` `RENDER_BG=black`

### `GET /healthz` → `{ status, device, asr_model, separation_model }`
### `GET /version` → app/model versions + git sha for reproducibility

## Swapping the ASR model

`ASR_MODEL` is read at load time. Vanilla Whisper is weak on Thai (PRD §6); swap
in a Thai-tuned model without code changes:
```bash
ASR_MODEL=biodatlab/whisper-th-large-v3-combined uvicorn app.main:app
```

## Thai alignment model (IMPORTANT)

**WhisperX ships NO default forced-alignment model for Thai** (verified — PRD §10
risk 3). Without a Thai wav2vec2 model, alignment fails and timing degrades to
segment-level (`aligned: false`). `ALIGN_MODEL` supplies one:
```bash
# default (gives real word-level timing):
ALIGN_MODEL=airesearch/wav2vec2-large-xlsr-53-th
# force the degraded segment-level path on purpose:
ALIGN_MODEL=none
```

## Tests

```bash
# fast, no models needed (API wiring / tokenize / mapping / LRC / ASS / command construction):
python -m pytest tests/test_api.py tests/test_pipeline_units.py -q

# M0 acceptance eval — needs the service running + 5 luk-thung stems in tests/samples/:
python tests/run_eval.py --url http://localhost:8000

# M1 manual acceptance — needs the service running; first request may download Demucs weights:
curl -s -X POST http://localhost:8000/separate -F "file=@song.mp3" -o stems.zip
```

## Constraints honored (PRD §5.1)

- Every stage has a working `--device cpu` path.
- GPU uses `int8_float16` to fit the 4 GB GTX 1650.
- Models are freed between stages — Demucs (M1) and Whisper never share the GPU.
- Stateless: audio is processed in a temp dir and deleted; never persisted.
