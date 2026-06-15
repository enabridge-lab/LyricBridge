# MODAL_RULES.md — Rules for writing Modal code in this repo

> Source: https://modal.com/docs/guide/developing-with-llms (fetched 2026-06-12).
> Machine-readable full docs: https://modal.com/llms-full.txt · examples: https://modal.com/docs/examples
> **Modal evolves fast and prints deprecation warnings on `modal run`** — when unsure of
> syntax, fetch the docs above. Never use a deprecated feature in new code.

## Project-specific conventions (LyricBridge — see `docs/MODAL_DEPLOYMENT.md`)

- **App name** = `lyricbridge` (kebab-case). Volumes/Secrets also kebab-case
  (`lyricbridge-models`, `lyricbridge-stems`, `lyricbridge-config`).
- **Workspace** = `chkrap47`. Backend URL will be `https://chkrap47--lyricbridge-web.modal.run`.
- **Region**: lock to broad Asia-Pacific → `region="ap"` on the GPU function.
  Owner-approved (1.5x price ≈ $0.075/song; narrow `ap-southeast` would be 1.75x).
- **GPU**: `gpu=["T4", "L4", "any"]`, `max_containers=1`, `timeout=900` — one job at a
  time preserves the VRAM invariant from `CLAUDE.md`.
- **Reuse, don't reinvent**: import the existing pipeline from `server/app` — do NOT copy
  pipeline code into `deploy/modal_app.py`.
- **Heavy imports (torch, faster-whisper, demucs) go INSIDE the function body**, never at
  global/module scope — global scope must import cleanly in every image + locally.
- **Two-container reality**: web (CPU) and GPU function are different machines, no shared
  disk → pass results via return values + `modal.Dict`. This is why F1 (m4a encode) is a
  hard prerequisite (small payloads fit in a Dict).
- **Stateless / no persisted audio**: temp dir + `finally` cleanup; Dict entries swept on a
  schedule (TTL). Matches PRD "don't persist user audio".
- **Environments**: `modal serve` (dev) stays in `dev`; production via `modal deploy --env prod`.

## General

- Modal is a serverless cloud platform for running Python with minimal config. You only
  pay for resources used (scale to zero).

## Core concepts

- **App** — a group of functions/classes/sandboxes deployed together (`modal.App()`).
- **Function** — basic unit of serverless execution; each runs in its own container with
  its own Image + hardware config. Invoke with `.remote()` (cloud, most common),
  `.local()`, `.map()` (parallel), `.spawn()` (fire-and-forget, returns a call handle).
- **Web Function** — expose HTTP via `@modal.fastapi_endpoint()` or `@modal.asgi_app()`.
- **Cls** (`@app.cls`) — stateful, with `@modal.enter()` / `@modal.method()` / `@modal.exit()`
  lifecycle hooks.
- **Image** — container image (`modal.Image.debian_slim(python_version="3.12")
  .apt_install(...).pip_install(...).env(...).add_local_python_source(...)`).
- **Volume** — distributed filesystem (model weights live here, `volume.commit()`).
- **Secret** — credentials/env injected into functions.
- **Dict** — distributed key/value store (we use it to pass stems web↔GPU).
- **Queue** — distributed FIFO queue.
- Schedules: `@app.function(schedule=modal.Period(hours=1))` or `modal.Cron("0 1 * * *")`.

## spawn + poll (the pattern this deploy depends on)

Web endpoints have a **150s HTTP limit** and the 303-redirect keep-alive does NOT work
across CORS. So a long `/karaoke` block is impossible. Instead: a CPU web endpoint calls
`gpu_fn.spawn(...)` → returns `call.object_id` as `job_id`; the browser polls
`GET /jobs/{id}` which does `modal.FunctionCall.from_id(id).get(timeout=0)` (202 if not
ready, payload when done). API shape mirrors F4 `/jobs/*` so one client serves self-host + Modal.

## Coding style

- Always `import modal`; use qualified names (`modal.App()`, `modal.Image.debian_slim()`).
- Apps, Volumes, Secrets → kebab-case.
- Put `import` statements for heavy deps inside the Function `def`, not global scope.

## Common commands (run on the OWNER'S machine — token lives in `~/.modal.toml`)

- `modal run path/to/app.py` — run an App on Modal (use `modal run -m module.path` for module path).
- `modal serve deploy/modal_app.py` — serve web functions with hot-reload (Ctrl+C to stop).
- `modal deploy deploy/modal_app.py` — deploy (use `--env prod` for production).
- `modal app logs <app_name>` — stream logs (Ctrl+C to stop; Starter retains logs ~1 day).
- Resource CLIs: `modal app list`, `modal volume list`, `modal secret list`, `modal dict list`, …
- `with modal.enable_output():` around `app.deploy()` for more output when debugging.

## Note for this agent / sandbox

The Cowork bash sandbox is a **separate machine** from the owner's box and does **not**
have the Modal token. All `modal run/serve/deploy/app list` commands must be executed by
the owner locally (or by CI in P1). The agent prepares code/files; the owner runs Modal.
