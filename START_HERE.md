# ▶ How to run LyricBridge (GPU)

## Start it
```bash
cd ~/enb/ai-karaoke
./scripts/run_gpu.sh
```
Then open **http://localhost:8080** and drop in a song.

That's it. The script:
- starts the web UI (:8080),
- starts the GPU API on the GTX 1650 (:8000),
- and prints `device: cuda ✅` when ready.

## Stop it
```bash
./scripts/stop_gpu.sh
```

## If something looks wrong
- Check the log:  `tail -f server/host_gpu.log`
- First song after starting is slower (models load once); later songs are fast.
- No GPU / want CPU instead?  `docker compose up -d`  (runs the CPU version in Docker).

## Notes
- The GPU API runs as a host process (not Docker) — that's the only way to use the
  GTX 1650 here. It does **not** auto-start on reboot, so run `./scripts/run_gpu.sh`
  again after a restart.
- For maximum speed (slightly lower quality), edit `scripts/run_gpu.sh` and change
  `SEPARATION_MODEL=htdemucs_ft.yaml` → `htdemucs.yaml`.
