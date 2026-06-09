#!/usr/bin/env bash
# 🛠  One-time setup for a fresh clone — builds the Python venv the GPU host API
#     needs (scripts/run_gpu.sh). The CPU path (`docker compose up`) does NOT
#     need this; Docker builds its own environment.
#
#   Usage:
#     ./scripts/setup.sh            # CPU deps (audio-separator[cpu])
#     ./scripts/setup.sh --gpu      # GPU deps (audio-separator[gpu], CUDA box)
#
#   Models are NOT downloaded here — they auto-download on the first song
#   (Whisper from HF, separator weights, wav2vec2 aligner). See
#   docs/REPRODUCIBLE_CLONE.md to optionally pre-fetch + pin them.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/server"

GPU=0
[ "${1:-}" = "--gpu" ] && GPU=1

echo "▶ Setting up server/.venv ($([ "$GPU" = 1 ] && echo GPU || echo CPU))…"

# 1) System prerequisite: ffmpeg (demux video, write wavs, render mp4) --------
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  ✗ ffmpeg not found. Install it first:" >&2
  echo "      sudo apt-get install -y ffmpeg fonts-thai-tlwg" >&2
  exit 1
fi

# 2) Virtualenv --------------------------------------------------------------
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip

# 3) Dependencies ------------------------------------------------------------
pip install -r requirements.txt
if [ "$GPU" = 1 ]; then
  # Swap the separator's CPU extra for the GPU (CUDA onnxruntime) one.
  pip install "audio-separator[gpu]"
fi

echo
echo "✅ Done.  Next:"
if [ "$GPU" = 1 ]; then
  echo "   ./scripts/run_gpu.sh      # GPU API on :8000 + web on :8080"
else
  echo "   docker compose up -d      # or run uvicorn from server/.venv on CPU"
fi
echo "   First song downloads the models (one-time), then it's cached."
