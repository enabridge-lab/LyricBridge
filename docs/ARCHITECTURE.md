# Architecture

> Status: M0 implemented (cloud ASR service). M1 server-side separation
> prototype added so the end-to-end pipeline can be validated before on-device
> separation. M2–M4 remain outlined in `../PRD.md` §8.

## System (target)

```
[user machine]                         [cloud]                       [user machine]
upload song → separate (on-device) → ┬─ Instrumental (stays local) ───────────────┐
                                     └─ Vocals ──→ Thai ASR → tokenize+align → LRC/ASS ┘
                                                                                     ↓
                                                            web player + video render
```

The split is deliberate (PRD §2, locked):
- **Separation is heavy/risky → runs on the user's machine.** Only the clean
  vocal stem is uploaded. Music never leaves the device → cheap server, free
  scaling, privacy.
- **The cloud service is stateless:** vocal `.wav` in → `LRC`/`ASS` out. No
  user audio is persisted.

## M0 — cloud ASR service (`server/`)

```
vocal.wav
  ├─[1] asr.py    faster-whisper (Thai-tuned via ASR_MODEL)   → segments
  ├─[2] align.py  WhisperX wav2vec2 forced alignment          → char timings
  │                 (degrades to None → segment-level timing if model missing)
  ├─[3] thai.py   PyThaiNLP newmm tokenize + map char→word    → Word spans
  └─[4] lrc.py    build LRC (line) + ASS (\k per-word sweep)   → response
```

Module map:
| file | responsibility |
|---|---|
| `app/main.py` | routes, temp-dir I/O, sequential stage orchestration, model freeing |
| `app/asr.py` | faster-whisper load (int8/int8_float16), device resolution |
| `app/align.py` | WhisperX alignment + graceful degradation |
| `app/thai.py` | tokenization + char-timing → word-span mapping |
| `app/lrc.py` | LRC + ASS rendering |
| `app/schemas.py` | pydantic API contract |

### Why both alignment AND tokenization?
ASR gives text. WhisperX gives precise time per **character**. PyThaiNLP tells us
where Thai **words** begin/end (Thai has no spaces). Combining them yields
accurate per-word start/end — the prerequisite for word-by-word highlighting.

### VRAM discipline (GTX 1650, 4 GB)
Stages run sequentially and free their model between steps, so Demucs (M1) and
Whisper never co-reside on the GPU. GPU uses `int8_float16`; CPU uses `int8`.
Every stage keeps a working CPU path.

## M1 — server-side separation prototype (`server/app/separate.py`)

The target architecture keeps separation on the user's machine, but M1 first
adds a server-side Demucs path so the product can validate:

```
song.mp3/mp4/wav -> Demucs two-stem separation -> vocals.wav + instrumental.wav
```

`POST /separate` returns a zip containing:
- `vocals.wav` — upload this to `/transcribe` for Thai ASR + timing.
- `instrumental.wav` — use this with the returned LRC/ASS in M2/M3.

Constraints carried forward from PRD §5.1:
- CPU is the self-host default and must keep working.
- GPU runs must use small Demucs segments (`DEMUCS_SEGMENT=7` by default).
- Separation and ASR are sequential; their models are never intentionally kept
  on the 4 GB GTX 1650 at the same time.

## Roadmap hooks
- **M2** `web/` player consumes `LRC` for real-time word highlight.
- **M3** ffmpeg burns the `ASS` file over the instrumental → karaoke `.mp4`.
- **M4** post-edit UI for luk-thung lyric/timing correction + self-host docs.
