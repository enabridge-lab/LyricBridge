# Copyright & Model Licenses

> Required honesty note (PRD §10, risk 4). Read before shipping a public demo.

## Songs belong to the user

**Separating a song into stems does NOT change the song's copyright status.**
Uploaded audio — and any vocal/instrumental stems derived from it — remains the
property of the original rights holder. This tool performs a technical
transformation; it confers no license to the underlying music.

Implications for self-hosters and the demo:
- Do not host or redistribute users' uploaded audio or derived stems.
- The cloud service is **stateless**: audio is processed in a temp dir and
  deleted immediately. We never persist user audio.
- A public demo (e.g. on `avocadu14.com`) should make clear that users are
  responsible for the rights to whatever they upload.

## Hosted demo on Modal (the public LyricBridge instance)

The hosted demo (`https://chkrap47--lyricbridge-web.modal.run`, frontend on GitHub
Pages) makes two things explicit, for honesty (PRD "document risks honestly"):

- **The "cloud gets only the vocal stem" locked decision (PRD §2) is WAIVED for the
  hosted demo only.** The demo runs the *whole* pipeline on Modal, so the **full song
  is uploaded** to the cloud. This waiver is **owner-approved** and applies *only* to
  the public demo — the **self-host path is unchanged** (separation stays on-device
  there). Separating stems still does not change the song's copyright; the uploader is
  responsible for the rights to whatever they submit.
- **No persisted audio.** The GPU worker deletes its temp dir in a `finally` block right
  after processing (the original upload never lives past one request). Only the generated
  m4a stems sit in a TTL store (`STEM_TTL_SEC`, default 30 min) so the player can fetch
  them, then a scheduled sweep purges them. The privacy note in the UI states this TTL.

## This project's license

**MIT** (see `../LICENSE`). You can self-host, modify, and redistribute the code.

## Third-party model licenses (must credit)

This project orchestrates models that carry their own licenses. When you ship,
credit them per their terms:

| Model / tool | Role | License notes |
|---|---|---|
| Demucs (HTDemucs v4) | separation (M1) | MIT (code); model weights per Meta's terms — credit required |
| UVR / MDX-Net | separation option (M1) | per UVR model card; credit required |
| faster-whisper / Whisper | ASR (M0) | MIT (faster-whisper); Whisper weights MIT |
| WhisperX | forced alignment (M0) | BSD-2; wav2vec2 align models per their cards |
| PyThaiNLP | Thai tokenization (M0) | Apache-2.0 |
| Typhoon ASR / GigaSpeech 2 (optional) | Thai-tuned ASR | per respective model cards — check before commercial use |

> If you swap `ASR_MODEL` to a Thai-tuned model, verify that model's license
> covers your use case (some research models restrict commercial use).
