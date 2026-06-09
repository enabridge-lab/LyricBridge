# M0 Eval Notes — 5-song vocal-stem run

> Fulfils the PRD §7.6 acceptance requirement: *"Document per-song notes: what
> drifted, where ASR mis-heard, alignment gaps."* This is the eyeball guide for
> the owner's **"พอใช้" go/no-go** — the one M0 gate a human must make.

## Run configuration

- **Input:** Demucs `htdemucs` vocal stems (GPU, `--segment 7`) of the 5 test
  songs → `server/tests/samples_vocals/`.
- **ASR:** `biodatlab/whisper-th-large-v3-combined` converted to CTranslate2
  float16 (`models/whisper-th-large-v3-ct2/`), loaded `int8_float16` on the
  GTX 1650. Default VAD.
- **Align:** WhisperX + `airesearch/wav2vec2-large-xlsr-53-th`, per-segment with
  sub-window splitting (≤20s) — OOM-free.
- **Repeat-loop guard:** `asr.collapse_repeats()` active (see below).
- **Outputs:** `server/tests/out_vocals_fixed/` (`.lrc` / `.ass` / `.json`).

## Headline numbers (LRCLIB ground truth, PRD §7.7)

| Song | CER | WER | line offset med/p90 | aligned | ref |
|---|---|---|---|---|---|
| Bodyslam — ความรัก | **19%** | 27% | 1.82 / 11.07 s | ✅ | found |
| Bodyslam — เปราะบาง | 31% | 45% | 2.61 / 5.85 s | ✅ | found |
| ต้องโทษดาว (Cover Night) | — | — | — | ✅ | no ref |
| รักโกรธ — Season Five | **39%** | 54% | 0.98 / 15.23 s | ✅ | found |
| อยากให้รู้ว่ารักเธอ — Season Five | 23% | 31% | 2.22 / 4.05 s | ✅ | found |

5/5 word-aligned, 100% of words carry timing (99% for รักโกรธ). CER 19–39% on
**stems** vs 55–73% on the earlier full-mix runs — separation is the dominant
lever, as the hybrid architecture predicted.

## Per-song notes

### Bodyslam — ความรัก (CER 19% — best)
Opening verses transcribe as coherent, correct lyrics. Minor drift mid-song
(`กล่าวผ่านมา`, `เพลาไข`) on busier passages. Timing tracks the line starts well
(median 1.8 s). **Reads as real lyrics.**

### Bodyslam — เปราะบาง (CER 31%)
Verses are good (`หนทางยังดูเหมือนเดิม…`). **Known hallucination:** line 4 injects
`วงข้าวเปลือก…ที่บริษัท A-TECH งานเลี้ยงบริษัท` — unrelated text fabricated over a
low-energy/instrumental gap (hallucination **class B**, see below). One bad line
in an otherwise usable transcript.

### ต้องโทษดาว — Cover Night Plus (no LRCLIB ref)
Clean pop cover → mostly coherent lyrics. **Known hallucination:** opens with a
`กรีนเวฟเพลงดีๆ…` radio-station ident bleeding in before the vocal starts
(class B). After that, the body reads well. No ground-truth ref in LRCLIB → relies
on owner judgment.

### รักโกรธ — Season Five (CER 39% — worst)
Real lyrics but the most drift: line 3 garbles/truncates
(`ถ้าใครมีของสักครั้งพิมพ์ใหม่…`) and short fragments mis-hear
(`คนจะรู้เขาไม่เคยรั้งกา`). Rock-ish delivery with melisma → harder for ASR.
Still recognisable as the song; **borderline "พอใช้".**

### อยากให้รู้ว่ารักเธอ — Season Five (CER 23%)
The **repeat-loop fix is visible here.** Line 2 previously was `จื๊ด`×60 (a
degenerate decoder loop over a falsetto/ad-lib section); it is now bounded text
(`ชื่อดึกชื่อๆชื่อจึงอยู่…`). Still *wrong* on that one hard section (that audio is
genuinely non-lexical), but no longer a catastrophic 60× loop that floods the
timeline. The rest reads as real lyrics. CER improved 37→23 with the loop garbage
removed.

## Two hallucination classes (be precise about what was/wasn't fixed)

- **Class A — degenerate repeat loops** (one short unit ×dozens, e.g. `จื๊ด`×60).
  **FIXED** this run by `asr.collapse_repeats()` + `_repeat_keep_policy()`:
  collapses tiny-unit loops to a 1-copy hint, spares real choruses
  (`รักเธอ`×5 preserved). Verified: 55→0 occurrences in อยากให้รู้, 0 loops across
  all 5 songs, 17/17 unit tests.
- **Class B — semantic hallucination on low-energy audio** (radio idents, invented
  phrases over instrumental gaps; เปราะบาง line 4, ต้องโทษดาว intro). **NOT fixed —
  out of scope for the loop guard.** This is PRD §10 risk 2 ("auto ASR on
  luk-thung won't be exact → ship post-edit in M4"). Candidate future mitigations
  (NOT done, would need their own validation so they don't drop real quiet
  vocals): `no_speech_threshold` / `logprob_threshold` filtering, tighter VAD on
  the stem, or a short post-edit pass.

## Owner sign-off checklist (the remaining M0 gate)

Listen to each song against `out_vocals_fixed/<song>.lrc` and judge **พอใช้ / not**:
- [ ] ความรัก  - [ ] เปราะบาง (ignore the A-TECH line)  - [ ] ต้องโทษดาว
- [ ] รักโกรธ (borderline)  - [ ] อยากให้รู้ว่ารักเธอ (check line 2 is tolerable)

If ≥ "พอใช้": M0's human gate passes → proceed to M1. If not: the lever is ASR
accuracy on hard sections (class B), i.e. a Thai-tuned model tweak or the M4
post-edit path — **not** the loop guard, which is done.
