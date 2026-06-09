#!/usr/bin/env bash
# ⏹ Stop LyricBridge (GPU mode).
#   Run:  ./scripts/stop_gpu.sh
#
# Tears down both halves that run_gpu.sh started:
#   1. the GPU API host process (uvicorn on :8000)
#   2. the web UI Docker container (:8080)
set -euo pipefail

# repo root = parent of this script's dir (scripts/..)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "⏹ LyricBridge — stopping…"

# 1) GPU API host process ----------------------------------------------------
if pkill -f "uvicorn app.main" 2>/dev/null; then
  echo "  ✓ GPU API stopped   (:8000)"
else
  echo "  · GPU API was not running"
fi

# 2) Web UI (and the CPU asr service, if it happened to be up) ---------------
docker compose stop web asr >/dev/null 2>&1 || true
echo "  ✓ web UI stopped     (:8080)"

echo
echo "✅ Stopped.  Run ./scripts/run_gpu.sh to start again."
