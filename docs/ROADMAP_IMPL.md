# ROADMAP_IMPL — detailed build spec for Claude Code

> Companion to `docs/ROADMAP_LANDING_AND_OAUTH.md` (the *what/why*). This is the *how* —
> concrete files, function names, data shapes, reuse points, gotchas, acceptance.
> **Anchors verified 2026-06-15** against `web/player.js`, `web/index.html`,
> `deploy/modal_app.py`, `server/app/{render,lrc,main,schemas}.py`. Don't guess; if an anchor
> drifted, re-read the file. **Locked decisions hold**: MIT, stateless, never persist user
> audio/stems/video. Keep Modal for GPU — no Railway, no DB (until explicitly approved).

## How the existing frontend works (read first)

`web/player.js` = one ES module. **Pure logic is exported** (unit-tested in
`web/player.test.mjs` via `node --test`); **DOM wiring lives in `init()`** guarded by
`typeof document`. Any new pure helper you add → export it + add a test.

Player runtime state (inside `init()`):
- `model = { words, lines }` from `buildModel(payload)`. Each word: `{text,start,end,confidence,interpolated,roman, _i}` where `_i` is a flat index.
- `wordSpans[_i]` = the rendered `<span class="word">` (built in `renderLyrics()`).
- **Highlight loop** = `highlight()` on `requestAnimationFrame`; lights the span at
  `activeWordIndex(model.words, withOffset(els.audio.currentTime, syncOffsetMs))`.
- **Instrumental** = `els.audio` (`<audio id="audio" controls>`). **Optional vocal guide** =
  `vocalAudio` (a 2nd `Audio`, kept in lockstep by `_wireVocalSync()` on play/pause/seek).
- Job flow: `runKaraoke(file)` → `submitKaraokeJob` → `pollKaraokeJob(onUpdate=showJobUpdate)`
  → `installKaraokeResult(payload, base, file)` (sets `els.audio.src = base+payload.instrumental_url`,
  `loadModel`, `renderJobId = payload.job_id`, reveals 🎬).
- Helpers to reuse: `download(name, data, type)`, `loadModel(payload, file)`,
  `setStatus(msg, kind)`, `showStage/showProcessing/_stepBar`, `serializeLines(lines)`.
- localStorage keys already in use: `lyricbridgeJob`, `syncOffsetMs`, `vocalGuideVol`,
  `showRoman`, `renderStyle`. Namespace new keys the same way.

Backend anchors:
- `deploy/modal_app.py`: `process_song` (GPU) writes `progress` Dict `{stage,step,total}` and
  stores stems in `stems` Dict as `{job_id}:instrumental|vocal → (bytes, expiry)`. `web` ASGI
  app: `/jobs/karaoke`, `/jobs/{id}` (`{status,stage,step,total,result,error}`),
  `/instrumental/{id}`, `/vocal/{id}`, `/render/{id}`, `/healthz`, `/metrics-lite`.
- `/render/{id}` → `render_from_job` builds ASS via `lrc.to_lines`/`to_ass` + `AssStyle`, then
  `render_job.remote(instrumental_bytes, ass_text, font)` (CPU ffmpeg). Style fields accepted:
  `font, font_size, primary_colour, highlight_colour, alignment, margin_v`.
- `server/app/render.py`: `ffmpeg_command(...)` builds the bg as `color=c=RENDER_BG:s=WxH` (lavfi)
  — this is the extension point for a background image (O1).

---

# Phase L — Landing page

**Files:** `web/index.html` (new landing), rename current player page → `web/app.html`,
edit `.github/workflows/deploy.yml`, maybe `web/style.css` (landing styles).

**Steps:**
1. `git mv web/index.html web/app.html` (the player page — keep the `lyricbridge-api-base`
   meta tag and `<script type="module" src="player.js?v=8">`; bump the `?v=`).
2. New `web/index.html` = landing. Sections (talebridge-mapped): hero (tagline + CTA
   `<a href="./app.html">ลองเลย — ฟรี</a>`), "ภาพรวมใน 1 นาที", 4 how-it-works cards
   (อัปเพลง→ตัดเสียงร้อง→ถอด+จับเวลา→ร้อง/เซฟวิดีโอ), feature highlights (🎯🇹🇭✏️🎤🎬),
   privacy/trust (reuse the footer copy already in app.html), open-source/self-host (GitHub link),
   CTA, footer. Static — no player.js needed; bilingual via existing `.en` span convention.
3. **CRITICAL — `deploy/deploy.yml` meta inject targets the player page.** The sed at the
   `Inject the Modal backend URL` step currently rewrites `web/index.html`; change it to
   `web/app.html`. If you miss this, the hosted player won't know its backend URL.

**Gotchas:** GitHub Pages serves at subpath `/LyricBridge/` → ALL links/assets relative
(`./app.html`, `./style.css`), never `/foo`. Hero CTA → `./app.html`.

**Acceptance:** Lighthouse a11y ≥ 90; from a phone open `…/LyricBridge/` → understand the
product → click CTA → `app.html` loads and (after deploy) `view-source` shows the injected
`lyricbridge-api-base` content = the Modal URL.

---

# Phase D — Demo & wait

## D1 — Pre-baked demo (no backend, no GPU)  ← highest conversion win
**Files:** `web/demo/demo.json` + `web/demo/demo.m4a`; `web/player.js`; landing/app button.
1. Generate once: run a real karaoke job on a **copyright-safe track** (CC-licensed or the
   owner's own recording — NEVER a copyrighted song). Save the `/jobs/{id}` `result` JSON →
   `demo.json`, and download `/instrumental/{id}` → `demo.m4a`.
2. Add a `loadDemo()` in `player.js` init: `fetch('./demo/demo.json')` → `loadModel(payload)`;
   `els.audio.src = './demo/demo.m4a'`; `document.body.classList.add('has-audio','has-lyrics')`;
   `renderJobId = null` (no parked instrumental → keep 🎬 hidden for the demo). Wire a
   "ดูตัวอย่าง / See a live example" button (on landing → links to `app.html?demo=1`; app reads
   the param and calls `loadDemo()`).
3. **⚠️ .gitignore GOTCHA:** the repo `.gitignore` blocks `*.m4a`, `*.wav`, `*.mp3`. The demo
   asset WILL be ignored. Add an exception so it commits:
   ```gitignore
   # demo asset is copyright-safe and intentionally committed
   !web/demo/demo.m4a
   ```
   (or `git add -f web/demo/demo.m4a`). Keep it small (<2–3 MB).

**Acceptance:** with the backend unreachable, open app → click "ดูตัวอย่าง" → karaoke plays with
word highlight; no network call to `/jobs/*`.

## D2 — Serve instrumental as soon as separation finishes (perceived speed)
**Files:** `deploy/modal_app.py` (`process_song`, `web` poll), `web/player.js` (`showJobUpdate`).
- In `process_song`, after `separate.separate(...)`: encode the instrumental m4a and
  `stems.put(f"{job_id}:instrumental", (inst, exp))` + `progress.put(job_id, {...,"instrumental_ready":True})`
  **before** calling `_run_pipeline`. (Vocal + final payload still come at the end.)
- In `web` `/jobs/{id}`: when not done but `progress.instrumental_ready`, include
  `"instrumental_url": f"/instrumental/{job_id}"` in the running response.
- In `player.js` `showJobUpdate(st)`: if `st.instrumental_url` and `els.audio` has no src yet,
  set `els.audio.src = base + st.instrumental_url` so the user can play music while lyrics finish.

**Gotcha:** single GPU container still runs one job; this is only a reordering of writes +
cleanup still in `finally`. **Acceptance:** poll body carries a playable `instrumental_url`
before `status==="done"`.

## D3 — ETA + lyrics trickling in
- `process_song` writes `progress.eta_sec` (rough: `duration_sec * k`, tune k per stage). Optional
  harder part: stream partial segments into `progress` so the player can show text appearing.
- `player.js` `showStage`: render the ETA next to the stage label. Minimal version = ETA only.

---

# Phase S — Stage / sing  (all client-side, no server cost)

## S1 — Fullscreen "stage" + 3-2-1 countdown + word sweep
**Files:** `web/app.html` (a button), `web/player.js`, `web/style.css`.
- Button "🎬 เต็มจอ / Stage mode" → `document.documentElement.requestFullscreen()` (needs a user
  gesture) + toggle `document.body.classList.toggle('stage-mode')` for big-type CSS. iOS Safari
  lacks element fullscreen → add a CSS-only full-viewport fallback under `.stage-mode`.
- Countdown: in `highlight()`, compute the next line's first `word.start`; if audio is playing and
  the gap to it is within ~4 s after a silent stretch, show a `3‑2‑1` overlay element.
- Word sweep/fill: extend `highlight()` to set a CSS var on the active span,
  `span.style.setProperty('--wprog', (t - w.start)/Math.max(0.05, w.end - w.start))`, and animate a
  fill in CSS (`.word.active { background: linear-gradient(...) }` driven by `--wprog`). Pure timing
  uses existing `activeWordIndex`/`model`.

**Acceptance:** fullscreen works on desktop (CSS fallback on iOS); countdown shows before verses;
the active word fills smoothly.

## S2 — Tempo (slow-to-practice) + A/B loop
**Files:** `web/app.html`, `web/player.js`.
- Tempo: `els.audio.playbackRate = r` and **keep guide in sync**: `vocalAudio.playbackRate = r`.
  Set `els.audio.preservesPitch = true` (+ `webkitPreservesPitch`, `mozPreservesPitch`) so slowing
  down doesn't pitch-shift.
- A/B loop: store `loopStart`/`loopEnd`; in `highlight()` (or `audio.ontimeupdate`), if
  `currentTime > loopEnd` set `currentTime = loopStart`. UI: "set A"/"set B"/"clear" buttons.

**Gotcha:** if a word's timing should track tempo — it does automatically, since highlight reads
`audio.currentTime` which advances at the playback rate. **Acceptance:** 0.5×–1.5× playback keeps
pitch + sync; loop repeats the chosen section.

## S3 — Player display theme (font size / colour / background)
- Distinct from F8 (which styles the *exported video*). This styles the *on-screen* player:
  body classes + `localStorage` key `playerTheme`. Reuse the show-roman class-toggle pattern.

## S4 — Record your own voice (signature feature, fully client-side)
**Files:** `web/app.html`, `web/player.js`. **No backend. The recording NEVER leaves the browser.**
- Web Audio graph: `const ctx = new AudioContext()`; `getUserMedia({audio:true})` → mic source;
  route `els.audio` through `ctx.createMediaElementSource(els.audio)`; mix mic + instrumental into
  `ctx.createMediaStreamDestination()`; `new MediaRecorder(dest.stream)` → collect chunks → Blob →
  `download('my-cover.webm', blob)`. Also connect instrumental to `ctx.destination` so the user
  still hears it.
- **Gotchas:** mic permission prompt; recommend headphones (avoid mic capturing the instrumental
  twice / echo); once an element is routed through a MediaElementSource it only plays via WebAudio
  — connect to `ctx.destination`; create the AudioContext on a user gesture.
- **Privacy:** add a line to the privacy footer — "การอัดเสียงทำในเบราว์เซอร์ ไม่ถูกส่งขึ้นเซิร์ฟเวอร์".

**Acceptance:** record while the instrumental plays → download a mixed file → Network tab shows
**no upload** of the recording.

---

# Phase E — Edit & reach

## E1 — Inline lyric editing (replace the prompt())
**Files:** `web/player.js` (`retypeWord`), maybe `style.css`.
- Today `retypeWord` uses `window.prompt` (clunky). Replace: on dblclick (edit mode), make the
  span `contentEditable` (or swap in a small `<input>`); commit on Enter/blur, cancel on Esc.
  On commit: `w.text = next; w.roman = null;` then `confirmWord(w, span)` (reuse existing). Keep the
  low-conf orange underline removal behavior (`confirmWord` already clears it).
- **Acceptance:** dblclick a 🟠 low-confidence word → edit inline → Enter saves → 🎬 re-render
  reflects the new text (it already serializes via `serializeLines`).

## E2 — EN translation toggle  (backend + cost — lower priority)
- Per-line Thai→English. Options: a server stage/model (adds GPU/CPU + a model download) or an
  external translation API (network + cost). **Flag against the $30 Modal budget.** Recommend:
  optional, cached, off by default, separate endpoint. Romanization (F7) already exists and is free
  — ship that reach win first; treat translation as a later, budgeted item.

---

# Phase O — Output & share

## O1 — Video background image / title card
**Files:** `server/app/render.py` (`ffmpeg_command`), `deploy/modal_app.py`
(`render_from_job` + `render_job`), `web/player.js` (render request), `web/app.html` (controls).
- `ffmpeg_command` builds bg as `color=c=RENDER_BG:s=WxH`. To support an image: add
  `-loop 1 -i <bg.jpg>` scaled/cropped to `RENDER_WIDTHxRENDER_HEIGHT` as the video base instead of
  the lavfi color; title card = a `drawtext` or a short lead-in. Keep it **parameterized + validated**
  (no raw user strings into the filter — validate like the font allowlist; ffprobe the image).
- Thread a new optional field through `/render/{job_id}` body → `render_from_job` →
  `render_job.remote(instrumental_bytes, ass_text, font, background_bytes)`.

**Acceptance:** render with a background image / title → mp4 shows it; bad image rejected pre-ffmpeg.

## O2 — Temporary share link  (⚠️ copyright-sensitive)
- **Never host the rendered mp4 or instrumental publicly** (contains the copyrighted track). Share
  **lyrics + timing only**: `POST /share/{job_id}` stores the payload JSON in a `shares` modal.Dict
  with a short TTL + random slug → returns `/s/<slug>`; `GET /s/<slug>` (or `app.html?share=<slug>`)
  loads it into the player (user supplies their own audio, or it's view-only text).
- Keep TTL short, slug unguessable, no public index/library. **Acceptance:** share link opens the
  lyrics view, expires on TTL; no audio/video is served from the share.

---

# Phase A — Google OAuth (stateless)

**Frontend (`web/app.html` + `player.js`):** add Google Identity Services script; render the
sign-in button; on credential callback keep the **ID token (JWT)** in memory; attach
`Authorization: Bearer <jwt>` to the `submitKaraokeJob` fetch (extend its signature to accept a
token). Landing + viewing the player + the demo (D1) need **no login**; only creating a karaoke job
is gated.

**Backend (`deploy/modal_app.py` `web`):** in `/jobs/karaoke`, read the Bearer token → verify the
JWT (signature against Google's JWKS — cache the certs; check `aud == GOOGLE_CLIENT_ID`, `iss`,
`exp`). Invalid → 401. Derive `google_sub`; enforce monthly quota in a `modal.Dict` keyed
`quota:{sub}:{YYYY-MM}` (reuse the rate-limit pattern, swap IP→sub). Over quota → 429
`stage:"queue"`. Add `GOOGLE_CLIENT_ID` to the `lyricbridge-config` Secret + a frontend config
(meta tag or build-time inject, like `lyricbridge-api-base`).

**No DB, no session store, no Railway.** Trade-offs (no history / no saved edits / Dict quota not
durable across redeploys) are documented + owner-accepted in `ROADMAP_LANDING_AND_OAUTH.md`.

**Acceptance:** not-logged-in can view landing/app/demo but "create" prompts login; logged-in can
create; over monthly quota → 429 with a clear message; no user data/audio persisted.

---

## Testing & ordering
- Every new **pure** function in `player.js` → export + cover in `web/player.test.mjs` (`node --test`).
- Backend changes → keep `pytest server/` green; the Modal `web`/`process_song` reorderings (D2) are
  covered by the D-phase curl acceptance.
- Suggested order (from the roadmap): **L + D1 → S → E1 → D2/D3 + O → A**. Each phase ships
  independently; none requires Railway or a DB.
