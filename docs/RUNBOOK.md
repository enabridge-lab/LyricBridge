# RUNBOOK — LyricBridge hosted demo (Modal) — P5

> Operational playbook for the public demo. Backend = Modal app `lyricbridge`
> (`https://chkrap47--lyricbridge-web.modal.run`); frontend = GitHub Pages.
> All `modal` commands run on a machine with the owner's token (`~/.modal.toml`)
> or in CI with `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`. Deploys run from repo root
> with `PYTHONPATH=server` (the pipeline lives in `server/app`).
>
> **Every deploy stamps the live commit:** `GET /healthz` → `{git_sha, accepting}`.
> So you can always tell what prod is running, and whether intake is paused.

## Live control knobs (no redeploy — take effect immediately)

The web app reads these per request from the `lyricbridge-control` Dict (→ Secret env
→ default). The CLI has no `dict put`, so write them with the `set_control` function:

```bash
# pause / resume intake (the $30 kill switch)
PYTHONPATH=server modal run deploy/modal_app.py::set_control --key ACCEPTING_JOBS --value 0
PYTHONPATH=server modal run deploy/modal_app.py::set_control --key ACCEPTING_JOBS --value 1
# tune caps live (empty value or unset → falls back to Secret/default)
PYTHONPATH=server modal run deploy/modal_app.py::set_control --key MAX_QUEUED --value 0
modal dict items lyricbridge-control     # inspect current overrides
```

Knobs: `ACCEPTING_JOBS`, `MAX_UPLOAD_MB`, `MAX_DURATION_SEC`, `RATE_LIMIT_PER_HOUR`,
`MAX_QUEUED`. `GIT_SHA` is also stamped here by CI (don't hand-edit unless rolling back).

---

## 1. Backend deploy broke prod

Starter has **no built-in rollback** → git-based.

1. Find the last-good commit (GitHub Actions history, or `git log --oneline`).
2. Redeploy that commit's code:
   ```bash
   git checkout <good-sha> -- deploy/ server/
   PYTHONPATH=server modal deploy deploy/modal_app.py
   PYTHONPATH=server modal run deploy/modal_app.py::set_control --key GIT_SHA --value <good-sha>
   ```
   (Or re-run that commit's `deploy` workflow from the GitHub Actions UI.)
3. Verify: `curl -s …/healthz | jq .git_sha` → equals `<good-sha>`.
4. Restore your working tree: `git checkout HEAD -- deploy/ server/`.

## 2. Frontend broke (GitHub Pages)

Pages has **no UI rollback** (unlike Cloudflare). Either:
- Actions → the `deploy` workflow → pick a known-good run → **Re-run jobs** (re-runs
  `deploy-frontend` with that commit's `web/`), **or**
- `git revert` the bad `web/**` change on `main` (re-triggers the workflow).

## 3. Free credit nearly exhausted / approaching $30

Modal does **not** auto-cut at a dollar amount. Enforcement = billing alert (warn) +
the kill switch (stop).

- Symptom: Modal billing alert (~$25, set in dashboard) fired, or jobs failing on quota.
- **Stop intake immediately, no redeploy:**
  ```bash
  PYTHONPATH=server modal run deploy/modal_app.py::set_control --key ACCEPTING_JOBS --value 0
  ```
  → `POST /jobs/karaoke` returns **503**; `/healthz` reports `accepting:false`; the
  frontend shows the "เดโมปิดชั่วคราว" banner. Re-enable next month with `--value 1`.
- Sanity-check spend/usage anytime: `GET /metrics-lite` (today's submitted/done/error +
  in-flight) and the Modal dashboard.

## 4. Modal outage

- Check https://status.modal.com.
- The frontend's load-time `/healthz` fetch fails silently (no banner) when the backend
  is unreachable; users just can't submit. Nothing to do but wait / post a status note.

## 5. Model Volume lost or corrupted

Repopulate `lyricbridge-models` (Thai CT2 + wav2vec2 aligner + htdemucs):
```bash
PYTHONPATH=server modal run deploy/modal_app.py::download_models
PYTHONPATH=server modal run deploy/modal_app.py::list_models   # verify caches non-empty
```
`ASR_MODEL` / `ASR_MODEL_REVISION` come from the `lyricbridge-config` Secret (pinned Thai
model). If the Secret is missing, recreate it (see §6 pattern) before downloading.

## 6. CI Modal token leaked (rotation)

No code change, no redeploy needed:
1. Modal dashboard → Settings → API Tokens → **revoke** the leaked token.
2. `modal token new` → new `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`.
3. GitHub → Settings → Secrets and variables → Actions → update both secrets.

To rotate the **config** Secret (e.g. change CORS or the Thai model), recreate it
(CORS/model are env-baked, so **redeploy** after):
```bash
modal secret create lyricbridge-config --force \
  ASR_MODEL=Avocaduu14/whisper-th-large-v3-ct2 \
  ASR_MODEL_REVISION=1a1554ea606d89c937216ada609bb8585e20a36e \
  ASR_DEVICE=auto SEPARATION_DEVICE=auto \
  CORS_ORIGINS=https://enabridge-lab.github.io \
  MAX_UPLOAD_MB=30 STEM_BITRATE=128k ACCEPTING_JOBS=1
PYTHONPATH=server modal deploy deploy/modal_app.py
```
> ⚠️ `--force` **replaces** the whole Secret — always include **all** keys, or you'll
> drop the ones you omit.

---

## Rollback drill (P5 acceptance — run once)

1. Note current prod sha: `curl -s …/healthz | jq .git_sha`.
2. Roll back to the previous commit per §1, set its `GIT_SHA`, confirm `/healthz` reports it.
3. Roll forward to `HEAD` (redeploy `main`), confirm `/healthz` matches again.
Record the exact commands + shas you ran here:

```
# (fill in during the drill)
# prod before:   <sha>
# rolled back to: <sha>   healthz confirmed: yes/no
# rolled forward: <sha>   healthz confirmed: yes/no
```
