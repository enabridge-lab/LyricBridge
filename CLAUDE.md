# CLAUDE.md — Operating guide for Claude Code

> Read this first, then read `PRD.md`. This file tells you *how to work on this repo*;
> `PRD.md` tells you *what to build*.

## What this project is

Open-source **LyricBridge**. User uploads any song → vocals are removed → word-synced
Thai lyrics are generated → play in a web player + export a karaoke video.
**Thai / luk-thung is the priority and the hard part.**

## Where to start

**Always start at Phase M0** (cloud ASR service) — see `PRD.md` §7.
M0 is the contract that validates the core risk: does auto-transcribing Thai/luk-thung actually work?
Do not jump ahead to UI (M2) or separation (M1) before M0 produces usable LRC/ASS on the 5-song test set.

**If the task is the production deploy** (Modal hosted demo): read `docs/MODAL_DEPLOYMENT.md`
(follow D0→D5→P1→P5 in order) and `docs/MODAL_RULES.md` (how to write Modal code in this repo)
**before touching `deploy/`**. All `modal` CLI commands run on the owner's machine, not the agent sandbox.

## Locked decisions — DO NOT change without owner sign-off

(Full table in `PRD.md` §2.)
- **Hybrid**: separation on-device, transcribe+sync on cloud. Cloud gets **only the vocal stem**.
- **Pure ASR** for lyrics — no online lyric fetch, no forced manual paste. (Accept luk-thung imperfection; ship post-edit as fast-follow.)
- **MVP output** = web player (real-time word highlight) **and** rendered karaoke video.
- **License = MIT.** Credit Demucs/UVR per their model licenses.
- **Build order** = M0 → M1 → M2 → M3 → M4.

## Thai rules you must not forget (PRD §6)

1. Thai has **no spaces between words** → always tokenize with **PyThaiNLP** before word-level highlight.
2. **Vanilla Whisper is weak on Thai** → use a Thai-tuned ASR (Typhoon / GigaSpeech 2), keep model name configurable via `ASR_MODEL` env.
3. **Luk-thung singing drifts ASR** (melisma/vibrato) → degrade gracefully and plan post-edit.

## Dev machine constraints (PRD §5)

Owner's box: Ubuntu 24.04, Ryzen 7 3750H, 32 GB RAM, **GTX 1650 — 4 GB VRAM**.
- **Never** run Demucs + Whisper on the GPU simultaneously. Sequential stages; free models between.
- faster-whisper: `compute_type="int8_float16"` (GPU) / `int8` (CPU). Full fp16 large-v3 won't fit.
- Demucs (M1): `--segment 7` or lower to avoid OOM.
- **Every stage must have a working `--device cpu` path** — self-hosters won't have a GPU, and 32 GB RAM covers CPU inference. Correctness before speed.

## Working agreements

- **Reuse, don't reinvent.** Build on [nomadkaraoke](https://github.com/nomadkaraoke) — use `python-audio-separator` and `python-lyrics-transcriber` rather than writing wrappers.
- **Don't persist user audio.** Cloud service is stateless; process in a temp dir and clean up.
- **Keep the self-host promise.** Cloud = one Docker image that runs on CPU; frontend = static site. `docker compose up` + static build must "just work."
- **Validate before expanding.** Each milestone has acceptance criteria in `PRD.md`. Meet them (especially the **M0 5-song luk-thung eval**) before moving on.
- **Document risks honestly** in `docs/` — especially that separating stems does not change song copyright.

## Repo layout

See `PRD.md` §4. M0 lives entirely in `server/`.

## When unsure

Stop and ask the owner before changing a locked decision (§2) or expanding scope.
The biggest failure mode for this project is scope creep before M0 is validated.
