"""WhisperX forced alignment -> char-level timings (PRD section 7.2 step 2).

KNOWN RISK (PRD section 10, risk 3): a Thai wav2vec2 alignment model may be
missing or weak. If load/align fails we must DEGRADE GRACEFULLY to segment-level
timing (return None) and log a warning -- never crash the request.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

from .asr import Segment, cuda_cleanup, is_oom_error, resolve_device
from .thai import CharTiming

logger = logging.getLogger(__name__)

_ALIGN_AVAILABLE: bool | None = None
# Last align-model LOAD failure reason (surfaced via /version so a silent
# degrade -- the #1 cause of "words don't track the audio" -- is visible).
_ALIGN_LOAD_ERROR: str | None = None


@dataclass
class AlignResult:
    """Outcome of align(): the char map plus how many segments degraded.

    `char_map` is None when nothing aligned (caller interpolates everything).
    `degraded_segments`/`total_segments` let /transcribe report how much of the
    song fell back to interpolation -- the key diagnostic for sync accuracy.
    """

    char_map: dict[int, list[CharTiming]] | None
    degraded_segments: int
    total_segments: int

# Explicit single-slot cache (not lru_cache) so free_model() can drop the last
# reference deterministically -- the wav2vec2-large aligner is ~2.9 GiB on the
# 4 GB GTX 1650, so a stale copy guarantees the next stage OOMs.
_ALIGN_MODEL = None
_ALIGN_META = None
_ALIGN_KEY: tuple[str, str] | None = None

# WhisperX ships NO default wav2vec2 align model for Thai (verified). Without an
# explicit Thai-finetuned model, alignment fails and we degrade to segment-level
# timing. Set ALIGN_MODEL to a HF wav2vec2 model to get real word-level timing;
# set ALIGN_MODEL=none to force the degraded path on purpose.
ALIGN_MODEL = os.getenv("ALIGN_MODEL", "airesearch/wav2vec2-large-xlsr-53-th")

# Align runs on its OWN device knob. The wav2vec2-large aligner is ~2.9 GiB; on
# the 4 GB GTX 1650 it can OOM after Demucs/Whisper. Set ALIGN_DEVICE=cpu to run
# JUST this stage on CPU -- slower, but it yields REAL char timing instead of
# silently degrading to interpolation. Empty -> follow the global ASR device.
ALIGN_DEVICE = os.getenv("ALIGN_DEVICE", "").strip()


def _align_device() -> str:
    """Device for the align stage: ALIGN_DEVICE if set, else the ASR device."""
    if ALIGN_DEVICE:
        return resolve_device(ALIGN_DEVICE)
    return resolve_device()


def align_load_error() -> str | None:
    """Reason the align model last failed to load (None if OK/untried)."""
    return _ALIGN_LOAD_ERROR

# wav2vec2 activation memory scales with the audio slice length. On the 4 GB
# GTX 1650 a single long sung line (luk-thung lines can run 30 s+) OOMs even
# after the per-segment fix. Segments longer than this are split into time
# sub-windows (text divided proportionally) so each forward pass stays small.
ALIGN_MAX_WINDOW_SEC = float(os.getenv("ALIGN_MAX_WINDOW_SEC", "20"))


def _load_align_model(language_code: str, device: str):
    global _ALIGN_MODEL, _ALIGN_META, _ALIGN_KEY
    key = (language_code, device)
    if _ALIGN_MODEL is not None and _ALIGN_KEY == key:
        return _ALIGN_MODEL, _ALIGN_META

    # A different key (or a stale model) is loaded -> release it before loading.
    if _ALIGN_MODEL is not None:
        free_model()

    global _ALIGN_LOAD_ERROR
    import whisperx

    cuda_cleanup()  # clear anything a crashed prior stage left on the GPU
    model_name = ALIGN_MODEL.strip()
    try:
        if model_name.lower() in ("", "none", "default"):
            # WhisperX's per-language default (will raise for Thai -> degrade).
            model, metadata = whisperx.load_align_model(
                language_code=language_code, device=device
            )
        else:
            model, metadata = whisperx.load_align_model(
                language_code=language_code, device=device, model_name=model_name
            )
    except Exception as e:
        # A LOAD failure means EVERY word degrades to interpolation -> make it
        # loud (ERROR, not the silent warning) and remember why for /version.
        _ALIGN_LOAD_ERROR = f"{type(e).__name__}: {e}"
        logger.error(
            "Align model FAILED to load (model=%s device=%s) -> ALL words will be "
            "interpolated, not aligned. Reason: %s. Fix: pre-download the model, "
            "check network/cache, or set ALIGN_DEVICE=cpu if this is GPU OOM.",
            model_name, device, _ALIGN_LOAD_ERROR,
        )
        raise
    _ALIGN_LOAD_ERROR = None  # loaded OK -> clear any stale error
    _ALIGN_MODEL, _ALIGN_META, _ALIGN_KEY = model, metadata, key
    return _ALIGN_MODEL, _ALIGN_META


def align_configured() -> bool:
    """Cheap, side-effect-free check: is a Thai align model configured?

    `/version` uses this instead of align_available() so a status hit never
    loads the ~2.9 GiB aligner. Reports intent (a model is set), not a verified
    load -- the real probe is align_available().
    """
    return ALIGN_MODEL.strip().lower() not in ("", "none")


def align_available(language_code: str = "th") -> bool:
    """Best-effort probe of whether an align model loads for this language.

    WARNING: heavy -- actually loads (then frees) the align model. Prefer
    align_configured() for status endpoints.
    """
    global _ALIGN_AVAILABLE
    if _ALIGN_AVAILABLE is not None:
        return _ALIGN_AVAILABLE
    try:
        _load_align_model(language_code, _align_device())
        _ALIGN_AVAILABLE = True
    except Exception as e:  # noqa: BLE001
        logger.warning("Align model unavailable for %s: %s", language_code, e)
        _ALIGN_AVAILABLE = False
    finally:
        # This is only a probe (e.g. from /version) -- never leave the ~2.9 GiB
        # aligner resident on the GPU. The next align() call reloads on demand.
        free_model()
    return _ALIGN_AVAILABLE


def align(
    wav_path: str,
    segments: list[Segment],
    language_code: str = "th",
) -> AlignResult:
    """Return char timings per segment + a degraded-segment count.

    Segments are aligned ONE AT A TIME (PRD section 5.1): aligning a whole song
    in a single whisperx.align() call holds every segment's wav2vec2 activations
    on the GPU at once and OOMs the 4 GB GTX 1650 on long tracks. Per-segment,
    the high-water mark is bounded by the longest single segment, and we free
    activations between segments. A segment that still OOMs degrades only itself
    (left unaligned -> caller interpolates that line), not the whole song.

    char_map=None is a valid, expected outcome -> caller interpolates everything.
    """
    total = len(segments)
    if not segments:
        return AlignResult(None, 0, 0)

    # Load the model once -- a hard failure here means no alignment at all
    # (every segment degrades). _load_align_model already logs ERROR + records
    # the reason for /version.
    try:
        import whisperx

        device = _align_device()
        try:
            model, metadata = _load_align_model(language_code, device)
        except Exception as e:  # noqa: BLE001
            # The ~2.9 GiB wav2vec2 aligner can OOM at load on the 4 GB GTX 1650
            # after Demucs/Whisper have fragmented the card. Rather than degrade
            # the WHOLE song to interpolation (the worst, *silent* failure mode),
            # retry this stage once on CPU -- slower, but yields REAL char timing.
            # Mirrors asr.transcribe's OOM->CPU fallback (PRD 5.1). `device` is
            # reused by the per-segment loop below, so the retry propagates.
            if is_oom_error(e) and device != "cpu":
                logger.warning(
                    "Align model OOM on %s; retrying this stage on CPU (slower, "
                    "but real timing beats silent interpolation). %s", device, e
                )
                free_model()  # drop the half-loaded GPU model + empty_cache
                device = "cpu"
                model, metadata = _load_align_model(language_code, device)
            else:
                raise
        audio = whisperx.load_audio(wav_path)  # CPU numpy; sliced per segment
    except Exception as e:  # noqa: BLE001
        logger.warning("Forced alignment unavailable, degrading whole song: %s", e)
        return AlignResult(None, total, total)

    out: dict[int, list[CharTiming]] = {}
    degraded = 0
    for i, s in enumerate(segments):
        windows = _split_windows(s.start, s.end, s.text, ALIGN_MAX_WINDOW_SEC)
        chars: list[CharTiming] = []
        win_failed = False
        for w_start, w_end, w_text in windows:
            try:
                chars.extend(
                    _align_window(
                        whisperx, model, metadata, audio, device,
                        w_start, w_end, w_text,
                    )
                )
            except Exception as e:  # noqa: BLE001
                win_failed = True
                logger.warning(
                    "Segment %d/%d window [%.1f-%.1f]s failed, interpolating: %s",
                    i + 1, len(segments), w_start, w_end, e,
                )
            finally:
                # Cap the high-water mark: release this window's activations
                # before the next forward pass.
                cuda_cleanup()
        if chars:
            out[i] = chars
        if win_failed and not chars:
            degraded += 1

    if degraded:
        logger.info(
            "Alignment degraded %d/%d segments to interpolated timing",
            degraded,
            len(segments),
        )
    # Empty map -> nothing aligned; report char_map=None so `aligned` is False.
    return AlignResult(out or None, degraded, total)


def _split_windows(
    start: float, end: float, text: str, max_sec: float
) -> list[tuple[float, float, str]]:
    """Split a segment into <=max_sec time windows with text divided pro-rata.

    Returns [(start, end, text), ...] spanning [start, end] with no gaps, so the
    concatenated char timings stay monotonic. Short segments pass through as one
    window. Text is split by character count -- approximate at boundaries, but
    every char lands in exactly one window so none are lost.
    """
    dur = max(0.0, end - start)
    if dur <= max_sec or len(text) <= 1:
        return [(start, end, text)]

    n = min(math.ceil(dur / max_sec), len(text))
    win = dur / n
    windows: list[tuple[float, float, str]] = []
    for k in range(n):
        w_start = start + k * win
        w_end = end if k == n - 1 else start + (k + 1) * win
        c0 = round(k * len(text) / n)
        c1 = len(text) if k == n - 1 else round((k + 1) * len(text) / n)
        chunk = text[c0:c1]
        if chunk:
            windows.append((w_start, w_end, chunk))
    return windows


def _align_window(
    whisperx, model, metadata, audio, device,
    start: float, end: float, text: str,
) -> list[CharTiming]:
    """Forced-align one (text, time-window) pair. May raise -> caller degrades."""
    result = whisperx.align(
        [{"start": start, "end": end, "text": text}],
        model,
        metadata,
        audio,
        device,
        return_char_alignments=True,
    )
    wx_segs = result.get("segments", [])
    return _chars_from_wx_segment(wx_segs[0]) if wx_segs else []


def _chars_from_wx_segment(seg: dict) -> list[CharTiming]:
    """Pull char timings out of a single WhisperX-aligned segment."""
    chars: list[CharTiming] = []
    for c in seg.get("chars", []) or []:
        ch = c.get("char", "")
        start = c.get("start")
        end = c.get("end")
        if ch.strip() == "" or start is None or end is None:
            continue
        chars.append(CharTiming(char=ch, start=float(start), end=float(end)))
    return chars


def free_model() -> None:
    """Release alignment model to free the 4 GB GPU (PRD section 5.1).

    Move the module off the GPU first, then drop the global refs so torch can
    reclaim the VRAM on the following cuda_cleanup().
    """
    global _ALIGN_MODEL, _ALIGN_META, _ALIGN_KEY
    try:
        if _ALIGN_MODEL is not None and hasattr(_ALIGN_MODEL, "to"):
            _ALIGN_MODEL.to("cpu")
    except Exception:
        pass
    _ALIGN_MODEL = None
    _ALIGN_META = None
    _ALIGN_KEY = None
    cuda_cleanup()
