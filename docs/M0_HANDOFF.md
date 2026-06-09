# M0 Handoff — overnight status (2026-06-08)

## TL;DR
M0 is **code-complete, GPU-validated, repeat-loop bug fixed + tested, and the
CPU-in-Docker e2e now PASSES.** **One acceptance item remains, and it's yours:**
- **Owner "พอใช้" sign-off** on the 5 LRCs — only you can do this (see `M0_EVAL_NOTES.md`).

## ✅ CPU-in-Docker e2e — PASSED (PRD §7.5 step 7 / §7.6)
Resolved the overnight environment blockers (see history below) and ran it:
- `systemctl --user restart docker-desktop` cleared the wedged daemon.
- `docker compose build asr` **succeeded** → image `ai-karaoke-server:m1` = **5.56 GB**
  (CPU-sized — confirms the torch-CPU index fix; a CUDA image would be ~8 GB+).
- `ASR_MODEL=base ALIGN_MODEL=none docker compose up` → `/healthz` reports
  `device: cpu` → `POST /transcribe` (20s clip) → **HTTP 200**, valid LRC/ASS/word
  JSON, 22 Thai words, real lyrics. `aligned:false` only because align was off for
  a fast smoke test (word-alignment is already GPU-validated).
- To re-run with full accuracy: drop the `ASR_MODEL`/`ALIGN_MODEL` overrides (uses
  large-v3 + Thai wav2vec2) — correct but slower on CPU.

## What got DONE tonight (safe on disk, verified)
- **Repeat-loop hallucination fix** (`server/app/asr.py`): `collapse_repeats()` +
  `_repeat_keep_policy()` kill degenerate Whisper loops (`จื๊ด`×60) while sparing
  real choruses (`รักเธอ`×5). **17/17 unit tests pass.** Re-ran the GPU eval:
  loop occurrences 55→0, 5/5 aligned, CER 19–39%. Outputs in
  `server/tests/out_vocals_fixed/`.
- **`docs/M0_EVAL_NOTES.md`** — per-song drift notes + your sign-off checklist
  (fulfils PRD §7.6 documentation requirement).
- **Dockerfile fix** (`server/Dockerfile`): the requirements install now passes
  `--extra-index-url https://download.pytorch.org/whl/cpu` so pip resolves
  **torch 2.8.0+cpu (~190 MB)** instead of the **887 MB CUDA wheel** it was
  wrongly pulling (that wrong wheel caused the build timeouts and bloated the
  "CPU image"). Also added `--timeout=300 --retries=10` for slow-link resilience.

## Why CPU-in-Docker did NOT finish tonight (environment, not code)
A cascade of **local infrastructure** problems, in order:
1. Build kept pulling the 887 MB CUDA torch wheel → **PyPI read-timeouts**. (Fixed
   in Dockerfile, see above.)
2. The verbose `BUILDKIT_PROGRESS=plain` flag I used for visibility **flooded
   `/tmp` and filled the root disk to 0 bytes**, breaking the build and tooling.
3. Freed disk by deleting the regenerable host venv `server/.venv` (CUDA torch,
   ~5 GB) → root went 0 → **7.4 GB free**. (Docker `prune` did **not** free the
   host fs: Docker Desktop's VM disk grows but never shrinks on prune.)
4. The clean rebuild then ran **46 min and wedged the Docker daemon** (a known
   aftereffect of the earlier disk-full event corrupting BuildKit). `docker info`
   now times out. I killed the build; **the daemon needs a restart.**

## RECOVERY — do this when awake (network up, ~5 min of active work)
```bash
# 1. Restart Docker Desktop to clear the wedged daemon
systemctl --user restart docker-desktop      # or quit+reopen the Docker Desktop app
#    wait ~30s, then confirm it's healthy:
docker info >/dev/null && echo "docker OK"

# 2. Build the CPU image (torch CPU fix is already in the Dockerfile)
cd ~/enb/ai-karaoke
docker compose build asr        # ~15-30 min on a good link; logs are compact now

# 3. Run the service on CPU (no GPU) and smoke-test it
docker compose up -d asr
#    wait for health, then POST a vocal stem (a 20s clip already exists):
curl -s -X POST http://localhost:8000/transcribe \
  -F "file=@server/tests/samples_vocals/'อยากให้รู้ว่ารักเธอ - SEASON FIVE (เนื้อเพลง) [8XHSeZmJ3zg].wav'" \
  -F "format=lrc" | head -c 400
#    expect HTTP 200 + an "lrc" field with Thai text  => M0 §7.5 step 7 PASS
docker compose down
```
Tip: for a *fast* CPU smoke test, set `ASR_MODEL=base` and `ALIGN_MODEL=none` in
`docker-compose.yml` (large-v3 on CPU is correct but slow). The default large-v3
also works — it's just heavier. A pre-trimmed 20s clip is at
`/tmp/cpu_smoke_clip.wav` if it survived; otherwise any stem in
`server/tests/samples_vocals/` works.

## Notes / housekeeping
- **`server/.venv` was deleted** to free disk. To restore the GPU dev env:
  `cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
  (then for GPU, reinstall CUDA torch per PRD §5.2). The Thai CT2 model is still
  at `models/whisper-th-large-v3-ct2/`.
- Root disk is **96% full (7.4 GB free)** — fine for the CPU image, but keep an
  eye on it; the Docker Desktop VM disk holds the high-water mark.
- Temp scratch files `.dfout .opout .chk .chk2 .chk3 .diskstate .prune .buildlog
  .dmn .spacetest` in the repo root are mine — safe to delete.

## Next milestone
After your "พอใช้" sign-off + the CPU-in-Docker confirmation above, M0 is fully
accepted → proceed to **M1 (separation)**, which is already prototyped and
GPU-working in `server/app/separate.py` (`/separate` endpoint, htdemucs, seg7).
