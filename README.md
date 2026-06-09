# 🎤 LyricBridge

[![English](https://img.shields.io/badge/Lang-English-2ea44f?style=for-the-badge)](README.md) [![ไทย](https://img.shields.io/badge/Lang-ไทย-lightgrey?style=for-the-badge)](README.th.md)

Turn any song into word-synced Thai karaoke. Upload a full song → the vocals are
removed → AI transcribes and time-aligns the lyrics → sing along in the browser
with each word lighting up on beat, or export a karaoke video.

Thai / luk-thung is the priority. Runs on **GPU** (fast) or **CPU** (self-host,
no GPU needed).

> The repo ships code only — the models (Thai Whisper, separator, aligner)
> **auto-download on the first song** and are cached. See
> [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md).

---

## ▶ Run on CPU (any machine, no GPU)

```bash
git clone <your-repo-url> lyricbridge && cd lyricbridge
docker compose up -d        # web :8080 + CPU API :8000
```
Open **http://localhost:8080** and drop in a song. The first song downloads the
models once (then it's cached); CPU is slower (separation is the bottleneck) but
needs no GPU. Full self-host guide: [`docs/RUN_IT_YOURSELF.md`](docs/RUN_IT_YOURSELF.md).

## ▶ Run on GPU (NVIDIA / CUDA)

```bash
sudo apt-get install -y ffmpeg fonts-thai-tlwg
./scripts/setup.sh --gpu    # one-time: build server/.venv with audio-separator[gpu]
./scripts/run_gpu.sh        # web :8080 + GPU API :8000  (prints device: cuda ✅)
./scripts/stop_gpu.sh       # stop
```

> The GPU API runs as a host process (the only way to reach the GTX 1650 here),
> so it does **not** auto-start on reboot — just run `./scripts/run_gpu.sh` again.
> First song after starting is slower (models load once); later songs are fast.

---

## How it works

```
full song ─▶ separate (Demucs)  ─▶ instrumental ─▶ 🎵 web player (word highlight)
                   └─▶ vocals ─▶ transcribe (Whisper-Thai) ─▶ align (wav2vec2-th)
                                       └─▶ tokenize (PyThaiNLP) ─▶ LRC / ASS ─▶ 🎬 video
```

One upload (`POST /karaoke`) runs the whole pipeline; the browser shows live
per-stage progress (แยกเสียง → ถอดเนื้อ → จับเวลา → สร้างไฟล์).

Full stage-by-stage walkthrough (bilingual TH/EN — every model, what it does, and
how it hands off): [`docs/PIPELINE.md`](docs/PIPELINE.md).

The Thai-tuned ASR model is set with `ASR_MODEL` (defaults to a Hugging Face repo,
auto-downloaded). Pin it to a commit with `ASR_MODEL_REVISION` for output that
stays identical over time — see [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md).

| Endpoint | Does |
|---|---|
| `POST /karaoke` | full song → instrumental + word-timed lyrics (one call) |
| `POST /separate` | song → vocals + instrumental stems |
| `POST /transcribe` | vocal stem → words / LRC / ASS |
| `POST /render` | instrumental + ASS → burned karaoke `.mp4` |
| `GET /healthz` `GET /version` | status (device, models, timings) |

## Speed

Separation dominates the time. Pick a model with `SEPARATION_MODEL`:

| Model | Quality | Speed | Notes |
|---|---|---|---|
| `htdemucs.yaml` *(default)* | good | fast | single Demucs model |
| `htdemucs_ft.yaml` | best | ~4× slower | 4-model ensemble (used on GPU here) |
| `UVR_MDXNET_KARA_2.onnx` | good vocals | fast | native 2-stem |

On the GTX 1650 a 20 s clip runs the whole pipeline in ~35 s (warm). On CPU,
separation alone is minutes — so the speed tuning + GPU path matter a lot.

## Tests

```bash
cd server && .venv/bin/python -m pytest -q     # backend (54 tests)
cd web && node --test                          # frontend (17 tests)
```

## Docs

- [`docs/PIPELINE.md`](docs/PIPELINE.md) — **bilingual (TH/EN)** full pipeline + models explainer
- [`docs/REPRODUCIBLE_CLONE.md`](docs/REPRODUCIBLE_CLONE.md) — clone→run, publish + pin the model, license
- [`docs/RUN_IT_YOURSELF.md`](docs/RUN_IT_YOURSELF.md) — full self-host + all env vars
- [`docs/PERFORMANCE_TUNING.md`](docs/PERFORMANCE_TUNING.md) — speed tuning spec
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design · [`docs/COPYRIGHT_AND_LICENSES.md`](docs/COPYRIGHT_AND_LICENSES.md) — legal notes
- [`PRD.md`](PRD.md) — product spec · [`CLAUDE.md`](CLAUDE.md) — build constraints

## License

MIT. Credit Demucs / UVR per their model licenses. **Separating stems does not
change a song's copyright** — only process audio you have the right to use.
