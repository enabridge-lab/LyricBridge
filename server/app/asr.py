"""ASR backend — faster-whisper (Thai-tuned model swappable via ASR_MODEL).

VRAM reality (PRD section 5.1): the dev box is a GTX 1650 with 4 GB.
  - GPU  -> compute_type="int8_float16"  (full float16 large-v3 may not fit)
  - CPU  -> compute_type="int8"          (always-works fallback, 32 GB RAM)

The model name is read from ASR_MODEL so we can swap in a Thai-tuned model
(Typhoon / GigaSpeech 2) without code changes. Default is whisper large-v3,
which works out of the box but is weaker on Thai (see PRD section 6).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

# Reduce CUDA fragmentation on the 4 GB GTX 1650 (PRD section 5.1). Must be set
# before torch initialises CUDA; torch is imported lazily below, so this wins.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

logger = logging.getLogger(__name__)

ASR_MODEL = os.getenv("ASR_MODEL", "large-v3")
ASR_DEVICE = os.getenv("ASR_DEVICE", "auto")  # auto | cuda | cpu

# Pin the model to an exact Hugging Face commit so output stays reproducible
# OVER TIME: if the published model is re-uploaded/updated later, a clone that
# pins ASR_MODEL_REVISION keeps loading the exact weights it was built against,
# instead of silently picking up new ones. Empty -> latest on the default branch
# (works, but NOT reproducible across model updates). Ignored for local-dir
# ASR_MODEL paths (only HF repo ids are versioned). Set it to the commit hash
# shown on the model's HF "Files and versions" page.
ASR_MODEL_REVISION = os.getenv("ASR_MODEL_REVISION", "").strip() or None

# VAD tuning. Silero's defaults are tuned for SPEECH and aggressively drop
# sustained sung vowels (luk-thung melisma -> ~95% of a song lost). Gentler
# defaults keep singing. Set VAD_FILTER=false to disable entirely (best when the
# input is already a clean vocal stem with little instrumental bleed).
VAD_FILTER = os.getenv("VAD_FILTER", "true").lower() in ("1", "true", "yes")
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.2"))
VAD_MIN_SILENCE_MS = int(os.getenv("VAD_MIN_SILENCE_MS", "700"))

# Repetition-loop guard. Whisper-family models loop-hallucinate on non-lexical
# audio (instrumental breaks, sustained melisma): one short unit repeated dozens
# of times, e.g. "จื๊ดจื๊ดจื๊ด..." ×60 (PRD section 6.3). condition_on_previous_text
# already suppresses the CROSS-segment drift; this guard cleans the INTRA-segment
# loop the decoder still emits inside one window. The trick is to spare LEGIT
# luk-thung/pop repetition ("รักเธอรักเธอรักเธอ" is a real chorus) -- so the
# detection is mechanical here, but the keep/collapse POLICY lives in
# _repeat_keep_policy() below and is the real quality lever.
REPEAT_MAX_UNIT = int(os.getenv("REPEAT_MAX_UNIT", "12"))  # longest unit we scan

# Decoder beam width. 5 is the accurate default; set ASR_BEAM_SIZE=1 (greedy) for
# a faster "fast mode" run at a small accuracy cost (PRD perf §3).
ASR_BEAM_SIZE = int(os.getenv("ASR_BEAM_SIZE", "5"))


@dataclass
class Segment:
    """One ASR segment: a line of text with coarse start/end (seconds)."""

    text: str
    start: float
    end: float


# Set True to force the next model load onto CPU (used by the OOM fallback so
# the reloaded model lands on CPU instead of OOMing the GPU again).
_FORCE_CPU = False


def resolve_device(requested: str = ASR_DEVICE) -> str:
    """Pick cuda only if explicitly asked or actually available."""
    if _FORCE_CPU or requested == "cpu":
        return "cpu"
    try:
        import torch

        if requested in ("auto", "cuda") and torch.cuda.is_available():
            return "cuda"
    except Exception:  # torch missing or broken -> CPU is the safe path
        pass
    return "cpu"


def _compute_type(device: str) -> str:
    # int8_float16 keeps large-v3 inside the 4 GB GTX 1650; int8 for CPU.
    return "int8_float16" if device == "cuda" else "int8"


# The loaded model is held in an explicit module global (not lru_cache) so
# free_model() can drop the *last* reference deterministically. lru_cache hides
# the object, leaving GPU release at the mercy of gc timing -- unreliable for the
# 4 GB GTX 1650 where a single stale model means the next stage OOMs.
_MODEL = None
_MODEL_DEVICE: str | None = None


def is_oom_error(exc: BaseException) -> bool:
    """True if an exception is a CUDA out-of-memory error.

    Catches torch.cuda.OutOfMemoryError and the RuntimeError("CUDA out of
    memory")/CTranslate2/onnxruntime variants, so a stage can fall back to CPU
    instead of failing the whole song on the 4 GB GTX 1650 (PRD 5.1)."""
    name = type(exc).__name__
    if name in ("OutOfMemoryError", "CudaError"):
        return True
    msg = str(exc).lower()
    return "out of memory" in msg or ("cuda" in msg and "memory" in msg)


def cuda_cleanup() -> None:
    """Force-release freed GPU memory. Safe no-op without torch/CUDA.

    faster-whisper weights live in CTranslate2's own allocator, so dropping the
    Python ref + gc.collect() is what frees them; torch.cuda.empty_cache() then
    returns torch's cached blocks to the driver.
    """
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except Exception:
        pass


def _load_model():
    global _MODEL, _MODEL_DEVICE
    if _MODEL is not None:
        return _MODEL, _MODEL_DEVICE

    from faster_whisper import WhisperModel

    device = resolve_device()
    compute_type = _compute_type(device)
    # Mop up any memory a previously crashed stage left behind before we load.
    cuda_cleanup()
    logger.info(
        "Loading ASR model=%s revision=%s device=%s compute_type=%s",
        ASR_MODEL,
        ASR_MODEL_REVISION or "(latest)",
        device,
        compute_type,
    )
    _MODEL = WhisperModel(
        ASR_MODEL,
        device=device,
        compute_type=compute_type,
        # Pins the HF download to a commit for reproducibility; faster-whisper
        # ignores it for a local-directory model path. None -> latest.
        revision=ASR_MODEL_REVISION,
    )
    _MODEL_DEVICE = device
    return _MODEL, _MODEL_DEVICE


def current_device() -> str:
    """Device the ASR model is (or would be) loaded on — for /healthz."""
    return resolve_device()


def _longest_repeat_run(text: str, max_unit: int = REPEAT_MAX_UNIT):
    """Find the longest run of an *immediately repeated* substring.

    Scans unit lengths 1..max_unit and returns the consecutive repetition that
    covers the most characters, as ``(unit, count, start, end)`` where
    ``text[start:end] == unit * count``. Returns ``None`` if nothing repeats.

    This is pure mechanics -- it finds *where* something repeats and *how many*
    times. Whether that repetition is a hallucinated loop or a real chorus is
    decided by _repeat_keep_policy(); keeping the two separate means the
    judgment call is one small, testable function.

        >>> _longest_repeat_run("จื๊ด" * 60)[1]       # count
        60
        >>> _longest_repeat_run("รักเธอ" * 5)[:2]      # (unit, count)
        ('รักเธอ', 5)
        >>> _longest_repeat_run("สวัสดีครับ")          # no repetition
        None
    """
    best = None  # (covered_chars, unit, count, start, end)
    n = len(text)
    for unit_len in range(1, max_unit + 1):
        i = 0
        while i + unit_len <= n:
            unit = text[i : i + unit_len]
            count = 1
            j = i + unit_len
            while text[j : j + unit_len] == unit:
                count += 1
                j += unit_len
            if count >= 2:
                covered = count * unit_len
                if best is None or covered > best[0]:
                    best = (covered, unit, count, i, j)
                i = j  # skip past this run; don't re-scan its interior
            else:
                i += 1
    if best is None:
        return None
    _covered, unit, count, start, end = best
    return unit, count, start, end


# Thai combining marks (vowels above/below + tone marks) attach to a base
# consonant and don't add a visual character. "จื๊ด" is 4 code points but only
# 2 base chars (จ, ด); "รักเธอ" is 5 base chars. Counting BASE chars (not raw
# len) is what makes the "tiny unit" test below correct for Thai.
_THAI_COMBINING = set("ัิีึืฺุู"
                      "็่้๊๋์ํ๎")

# Tunable thresholds (env-overridable) for _repeat_keep_policy.
REPEAT_TINY_BASE = int(os.getenv("REPEAT_TINY_BASE", "2"))    # <= this = "tiny" unit
REPEAT_TINY_MIN = int(os.getenv("REPEAT_TINY_MIN", "4"))      # tiny loop collapses at >= this
REPEAT_HARD_MIN = int(os.getenv("REPEAT_HARD_MIN", "8"))      # any unit collapses at >= this


def _base_char_len(unit: str) -> int:
    """Count base (non-combining) Thai characters, so melisma loops with stacked
    tone/vowel marks aren't mistaken for long phrases."""
    return sum(1 for ch in unit if ch not in _THAI_COMBINING) or len(unit)


def _repeat_keep_policy(unit: str, count: int) -> int | None:
    """Decide whether a consecutive repetition is a hallucinated loop, and how
    much of it to keep.

    Called with a unit string and how many times it repeats back-to-back
    (e.g. unit="จื๊ด", count=60  OR  unit="รักเธอ", count=5).

    Returns ``None`` to leave the repetition untouched (legitimate lyric), or an
    ``int`` N to collapse the run to N copies of ``unit``.

    Default heuristic (tune the REPEAT_* envs above):
      - A *tiny* unit (<=2 base chars) looping >=4x is a melisma/instrumental
        hallucination -> keep 1 copy (a hint that *something* was vocalised).
      - *Any* unit repeated >=8x is past what a real sung hook does -> keep 2.
      - Otherwise it's a plausible chorus/ad-lib -> keep all (None).
    """
    if _base_char_len(unit) <= REPEAT_TINY_BASE and count >= REPEAT_TINY_MIN:
        return 1
    if count >= REPEAT_HARD_MIN:
        return 2
    return None


def collapse_repeats(text: str) -> str:
    """Apply _repeat_keep_policy to the longest repetition run in `text`."""
    run = _longest_repeat_run(text)
    if run is None:
        return text
    unit, count, start, end = run
    keep = _repeat_keep_policy(unit, count)
    if keep is None or keep >= count:
        return text
    return text[:start] + unit * max(keep, 0) + text[end:]


def transcribe(wav_path: str, lang: str = "th") -> list[Segment]:
    """Transcribe a vocal wav into Thai segments with coarse timing.

    Returns segment-level text + timing. Word-level precision is added later
    by align.py (forced alignment) + thai.py (tokenization).
    """
    try:
        return _transcribe_on(wav_path, lang, _load_model()[1])
    except Exception as exc:  # noqa: BLE001
        # On CUDA OOM, free the GPU model and retry once on CPU (PRD 5.1) -- a
        # slow transcript beats failing the whole song on the 4 GB card.
        if is_oom_error(exc) and resolve_device() != "cpu":
            logger.warning("ASR OOM on GPU; retrying on CPU (slower). %s", exc)
            free_model()
            global _FORCE_CPU
            _FORCE_CPU = True
            try:
                return _transcribe_on(wav_path, lang, "cpu")
            finally:
                _FORCE_CPU = False
        raise


def _transcribe_on(wav_path: str, lang: str, device: str) -> list[Segment]:
    model, _ = _load_model()
    segments_iter, _info = model.transcribe(
        wav_path,
        language=lang,
        vad_filter=VAD_FILTER,
        vad_parameters={
            "threshold": VAD_THRESHOLD,
            "min_silence_duration_ms": VAD_MIN_SILENCE_MS,
        },
        beam_size=ASR_BEAM_SIZE,
        condition_on_previous_text=False,  # luk-thung repeats; avoid drift loops
    )
    out: list[Segment] = []
    for s in segments_iter:
        text = (s.text or "").strip()
        text = collapse_repeats(text)  # kill intra-segment hallucination loops
        if not text:
            continue
        out.append(Segment(text=text, start=float(s.start), end=float(s.end)))
    return out


def free_model() -> None:
    """Release the ASR model so the GPU is free for the next stage.

    PRD section 5.1: never hold Demucs + Whisper on the 4 GB GPU at once.
    Dropping the global is what lets CTranslate2 free its weights; cuda_cleanup()
    then collects and returns the memory to the driver.
    """
    global _MODEL, _MODEL_DEVICE
    _MODEL = None
    _MODEL_DEVICE = None
    cuda_cleanup()
