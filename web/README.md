# web/ — M2 web player

Static, dependency-free karaoke player. Plays an **instrumental** track and
highlights the lyrics **word-by-word** in real time from a `/transcribe`
response. No build step, no framework — honours the PRD self-host promise
("frontend is a static site").

## Run it

```bash
# from repo root
python -m http.server 8000 --directory web
# open http://localhost:8000
```

1. **Instrumental audio** — the `instrumental.wav` from `POST /separate` (M1),
   or any audio of the song.
2. **Lyrics JSON** — the JSON body from `POST /transcribe` (M0). The sample
   outputs in `server/tests/out_vocals_fixed/*.json` work directly.

Press play; each word lights up as the audio reaches it and the current line
scrolls into view.

> Serve over HTTP, not `file://` — ES modules are blocked on `file://`.

## Post-edit (M4 — the luk-thung correction path)

Auto ASR on luk-thung won't be exact (PRD §10). Tick **Edit mode** to fix it:

- **Click a word** → its start time snaps to the current playhead (tap-to-sync).
  Play up to where a word *should* begin, click it, and the timing is corrected;
  the line stays non-overlapping automatically.
- **Double-click a word** → retype mis-heard Thai text.
- **Export LRC** / **Export JSON** → download the corrected files. The JSON is
  the same shape `/transcribe` returns (`edited: true`), so it feeds straight
  back into the player or `POST /render`.

## What it consumes

The existing `/transcribe` contract, unchanged:

```json
{ "duration_sec": 316.67, "aligned": true,
  "words": [ { "text": "เมื่อ", "start": 4.91, "end": 5.67 }, ... ],
  "lrc": "[00:04.91]…\n[00:55.33]…" }
```

`words[]` drives the word highlight; the `lrc` line timestamps drive the line
grouping (see `buildModel` in `player.js`).

## Tests

- **Logic:** `node --test` (from `web/`) — `parseLrc`, `groupWordsIntoLines`,
  `activeWordIndex`, `buildModel`.
- **Browser sync (headless):** drives real Chrome, feeds a sample JSON + silent
  wav, seeks the audio and asserts the highlighted word matches the timing data:
  ```bash
  # from repo root (needs server/.venv with playwright)
  PW_URL=http://localhost:8153/ \
  server/.venv/bin/python ~/.claude/skills/webapp-testing/scripts/with_server.py \
    --server "server/.venv/bin/python -m http.server 8153 --directory web" --port 8153 \
    -- env PW_URL=http://localhost:8153/ server/.venv/bin/python web/_headless_check.py
  ```

## PRD §8 M2 acceptance

Pass = "lyrics scroll on-beat in the browser." The headless check proves the
highlight tracks audio time and lines render correctly; the final visual
judgement (does it *feel* on-beat / readable) is the owner's eyeball gate.

## Not yet (future increments)

- Wire the upload flow directly to the live server (`/separate` → `/transcribe`)
  so a user drops in a song instead of two files. M2 core is the player itself;
  on-device separation is explicitly "M2+" in the PRD.
- Tunable line width / font size controls.
