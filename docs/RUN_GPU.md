# `scripts/run_gpu.sh` — GPU launcher

One command that brings LyricBridge up on the **GTX 1650 (4 GB VRAM)**: web UI in
Docker, transcription/separation API as a native CUDA process.

```bash
./scripts/run_gpu.sh
# → open http://localhost:8080 and drop in a song
```

For the casual "just run it" version, see [`START_HERE.md`](../START_HERE.md).
This file explains **what the script actually does** and how to debug it.

---

## Why the API runs on the host (not in Docker)

The shipped Docker API image is **CPU-only** (torch-CPU, ~190 MB). The only way to
reach the GTX 1650 on this box is a native process with CUDA torch in
`server/.venv`. So the script splits the stack:

| Component | Runs as | Port | Device |
|-----------|---------|------|--------|
| Web UI (static nginx) | Docker (`docker compose up -d web`) | 8080 | — |
| ASR / separation / align API | Host process (`.venv/bin/uvicorn`) | 8000 | **cuda** |
| CPU API (`asr` service) | Docker — **stopped** by this script | 8000 | cpu |

Both APIs want port 8000, so the script makes sure only one owns it at a time.

---

## What it does, step by step

1. **Resolve repo root.** `ROOT` is computed from `${BASH_SOURCE[0]}/..`, so the
   script works no matter where you call it from. Runs under `set -euo pipefail`.
2. **Start the web UI** — `docker compose up -d web` on `:8080` (errors ignored if
   already running).
3. **Free port 8000** — `docker compose stop asr` shuts the CPU Docker API down so
   it can't collide with the GPU host API.
4. **Pre-flight the venv** — if `server/.venv/bin/uvicorn` is missing it exits 1
   (the GPU venv isn't installed — see [Setup](#setup-the-gpu-venv)).
5. **Kill stale host API** — `pkill -f "uvicorn app.main"` prevents a double-bind on
   8000, then sleeps 1s.
6. **Launch the GPU API** — `nohup … uvicorn app.main:app --host 0.0.0.0 --port 8000`
   in the background, with the env below. Output goes to `server/host_gpu.log`.
7. **Health-check & confirm GPU** — polls `GET /healthz` for up to 60s
   (30 × 2s). It reads `device` from the JSON; if it isn't `cuda` the script exits 1
   so a silent CPU fallback never passes unnoticed.

## Environment it sets

| Var | Value | Meaning |
|-----|-------|---------|
| `SEPARATION_DEVICE` / `ASR_DEVICE` / `ALIGN_DEVICE` | `cuda` | run every stage on the GPU |
| `ASR_MODEL` | env → local `models/whisper-th-large-v3-ct2` → HF id | Thai-tuned CT2 Whisper. The script honours an `ASR_MODEL` override, else uses the local dir if present, else downloads the published HF model (see [`REPRODUCIBLE_CLONE.md`](REPRODUCIBLE_CLONE.md)). |
| `ASR_MODEL_REVISION` | `${ASR_MODEL_REVISION:-}` (latest) | Pin the HF model to a commit hash for reproducible output over time. |
| `SEPARATION_MODEL_DIR` | `models/audio-separator` | local separator weights |
| `SEPARATION_MODEL` | `htdemucs_ft.yaml` | best quality (slower) |
| `ALIGN_MODEL` | `airesearch/wav2vec2-large-xlsr-53-th` | Thai forced-aligner |
| `RENDER_FONT` | `Noto Sans Thai` | legible Thai in the rendered video |
| `EXPOSE_TIMINGS` | `1` | include per-word timings in the API response |
| `LOG_LEVEL` | `INFO` | API log verbosity |

> **VRAM discipline (PRD §5.1):** stages still run **sequentially** with models freed
> between them — the 4 GB card cannot hold separation + Whisper at once. A CUDA OOM in
> any stage auto-retries that stage on CPU. See [`PIPELINE.md`](PIPELINE.md) for stage details.

---

## Setup: the GPU venv

Step 4 fails if `server/.venv` isn't a CUDA env. Build it with the setup script:

```bash
./scripts/setup.sh --gpu          # creates server/.venv + audio-separator[gpu]
```

Or by hand (equivalent):

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # pulls CUDA torch (~887 MB)
.venv/bin/pip install "audio-separator[gpu]"
```

(The CPU instructions in [`RUN_IT_YOURSELF.md`](RUN_IT_YOURSELF.md) add
`--extra-index-url …/whl/cpu` to stay CPU-only — **omit that** for GPU.)

---

## Stopping it

Use the companion script — it tears down both halves the launcher started (the GPU
host API **and** the web container):

```bash
./scripts/stop_gpu.sh
```

If you ever need to stop them by hand:

```bash
pkill -f "uvicorn app.main"     # GPU API host process (:8000)
docker compose stop web         # web UI container (:8080)
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `server/.venv missing` | GPU venv not installed — run `./scripts/setup.sh --gpu` (see [Setup](#setup-the-gpu-venv)). |
| `API came up on device='cpu'` | CUDA torch not in the venv, or no GPU visible. Check `nvidia-smi` and `tail server/host_gpu.log`. |
| `device='?'` after the wait loop | API never answered in 60s. Watch startup: `tail -f server/host_gpu.log`. |
| Port 8000 already in use | A stray host API or the Docker `asr` service. Re-running the script clears both. |
| First song is slow | Models load once per process start; subsequent songs are fast. |

**Notes**
- The GPU API does **not** auto-start on reboot — re-run `./scripts/run_gpu.sh`.
- For max speed (slightly lower quality), set `SEPARATION_MODEL=htdemucs.yaml`.
- No GPU? Run the all-Docker CPU stack instead: `docker compose up -d`.

## See also
- [`START_HERE.md`](../START_HERE.md) — minimal quickstart
- [`RUN_IT_YOURSELF.md`](RUN_IT_YOURSELF.md) — full self-host / env-var reference
- [`PIPELINE.md`](PIPELINE.md) — full pipeline, VRAM discipline, OOM fallback
