#!/usr/bin/env bash
# ▶ Start LyricBridge on the GPU (GTX 1650).
#   Run:  ./scripts/run_gpu.sh
#   Then open:  http://localhost:8080
#
# What it does:
#   1. starts the web UI (Docker nginx on :8080)
#   2. makes sure the CPU Docker API is stopped (frees port 8000)
#   3. starts the GPU API on the host (:8000) with CUDA
set -euo pipefail

# repo root = parent of this script's dir (scripts/..)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "▶ LyricBridge — starting on GPU…"

# 1) Web UI (static site in Docker) -----------------------------------------
# --no-deps: start ONLY web. `web` depends_on `asr`, which has build:./server,
# so a plain `up web` would build/start the CPU API too (slow + fights :8000).
docker compose up -d --no-deps web >/dev/null 2>&1 || true
echo "  ✓ web UI            http://localhost:8080"

# 2) Make sure the CPU Docker API isn't holding port 8000 (in case it was up) -
docker compose stop asr >/dev/null 2>&1 || true

# 3) GPU API on the host ----------------------------------------------------
if [ ! -x server/.venv/bin/uvicorn ]; then
  echo "  ✗ server/.venv missing — the GPU venv isn't installed." >&2
  echo "    Run once:  ./scripts/setup.sh --gpu   (see docs/REPRODUCIBLE_CLONE.md)" >&2
  exit 1
fi

# ASR model resolution (reproducible across machines):
#   1. honour an explicit ASR_MODEL env override;
#   2. else use the locally-converted Thai model if it's on disk (owner's box —
#      no multi-GB download);
#   3. else fall back to the published HF model (a fresh clone auto-downloads it).
LOCAL_TH_MODEL="$ROOT/models/whisper-th-large-v3-ct2"
if [ -n "${ASR_MODEL:-}" ]; then
  ASR_MODEL_RESOLVED="$ASR_MODEL"
elif [ -d "$LOCAL_TH_MODEL" ]; then
  ASR_MODEL_RESOLVED="$LOCAL_TH_MODEL"
else
  ASR_MODEL_RESOLVED="champkrap/whisper-th-large-v3-ct2"
fi
echo "  · ASR_MODEL = $ASR_MODEL_RESOLVED  (revision: ${ASR_MODEL_REVISION:-latest})"

# stop any old host API so we don't double-bind :8000
pkill -f "uvicorn app.main" 2>/dev/null || true
sleep 1

cd server
nohup env \
  SEPARATION_DEVICE=cuda ASR_DEVICE=cuda ALIGN_DEVICE=cuda \
  ASR_MODEL="$ASR_MODEL_RESOLVED" \
  ASR_MODEL_REVISION="${ASR_MODEL_REVISION:-}" \
  SEPARATION_MODEL_DIR="$ROOT/models/audio-separator" \
  SEPARATION_MODEL=htdemucs_ft.yaml \
  ALIGN_MODEL=airesearch/wav2vec2-large-xlsr-53-th \
  RENDER_FONT="Noto Sans Thai" \
  EXPOSE_TIMINGS=1 LOG_LEVEL=INFO \
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  > "$ROOT/server/host_gpu.log" 2>&1 &
cd "$ROOT"

# 4) Wait for it to answer, confirm GPU -------------------------------------
echo -n "  … waiting for GPU API"
for i in $(seq 1 30); do
  if curl -s http://localhost:8000/healthz >/dev/null 2>&1; then break; fi
  echo -n "."; sleep 2
done
echo
DEV=$(curl -s http://localhost:8000/healthz | python3 -c "import json,sys;print(json.load(sys.stdin)['device'])" 2>/dev/null || echo "?")
if [ "$DEV" = "cuda" ]; then
  echo "  ✓ GPU API           http://localhost:8000   (device: cuda ✅)"
  echo
  echo "✅ Ready!  Open  http://localhost:8080  and drop in a song."
else
  echo "  ✗ API came up on device='$DEV' (expected cuda). Check server/host_gpu.log" >&2
  exit 1
fi
