"""Thai tokenization + word-timing mapping (PRD section 7.2 step 3).

Thai has no spaces between words, so WhisperX char timings must be regrouped
into real word spans before per-word highlighting is possible.

Two jobs:
  - tokenize(text)               -> list[str]   (PyThaiNLP newmm)
  - map_words(tokens, ...)       -> list[Word]  (attach start/end to each token)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .schemas import Word

logger = logging.getLogger(__name__)

# F7: romanization engine for Word.roman. royin (Royal Institute standard) is
# rule-based and ships inside pythainlp — no extra packages needed.
ROMANIZE_ENGINE = os.getenv("ROMANIZE_ENGINE", "royin")
_romanize_warned = False


def romanize_word(text: str) -> str:
    """Romanized reading of a Thai word, or "" when the engine can't.

    Degrades, never raises: a broken/missing engine logs ONE warning and then
    returns "" for everything (the pipeline and player work fine without it).
    """
    global _romanize_warned
    try:
        from pythainlp.transliterate import romanize

        return romanize(text, engine=ROMANIZE_ENGINE) or ""
    except Exception as e:  # noqa: BLE001
        if not _romanize_warned:
            logger.warning(
                "romanize engine %r unavailable; Word.roman disabled: %s",
                ROMANIZE_ENGINE, e,
            )
            _romanize_warned = True
        return ""


@dataclass
class CharTiming:
    """One aligned character with its time span (from WhisperX)."""

    char: str
    start: float
    end: float


# Thai combining marks (vowels above/below + tone marks). WhisperX is
# inconsistent about emitting these as their own timed chars, so we match on
# BASE characters only -- skip combining marks on both the token and the timed
# stream -- to avoid a missing mark derailing the greedy walk.
_THAI_COMBINING = set("ัิีึืฺุู" "็่้๊๋์ํ๎")


def _is_combining(ch: str) -> bool:
    return ch in _THAI_COMBINING


def tokenize(text: str) -> list[str]:
    """Split Thai text into words with PyThaiNLP newmm.

    newmm is fast, dictionary-based, and the pragmatic default. Empty/space
    tokens are dropped so they never become zero-width highlight targets.
    """
    from pythainlp.tokenize import word_tokenize

    tokens = word_tokenize(text, engine="newmm", keep_whitespace=False)
    return [t for t in tokens if t.strip()]


def _interpolate(tokens: list[str], seg_start: float, seg_end: float) -> list[Word]:
    """Fallback timing: spread a segment's duration across tokens by length.

    Used when no char-level alignment is available (Thai align model missing,
    PRD section 10 risk 3). Coarse but always monotonic and non-overlapping.
    """
    if not tokens:
        return []
    weights = [max(len(t), 1) for t in tokens]
    total = sum(weights)
    span = max(seg_end - seg_start, 0.0)
    words: list[Word] = []
    cursor = seg_start
    for tok, w in zip(tokens, weights):
        dur = span * (w / total)
        # F6: the whole segment's timing is guessed — flag every word.
        words.append(
            Word(text=tok, start=round(cursor, 3), end=round(cursor + dur, 3),
                 interpolated=True)
        )
        cursor += dur
    return words


def map_words(
    tokens: list[str],
    seg_start: float,
    seg_end: float,
    char_timings: list[CharTiming] | None = None,
) -> list[Word]:
    """Attach a start/end to each Thai token.

    With char_timings: greedily walk the timed-character stream to find each
    token's span; tokens whose chars don't align are left as gaps and then
    LINEARLY interpolated between their matched neighbours (so an unmatched word
    gets a real, increasing span instead of collapsing to a zero-width point).
    Without char_timings: proportional interpolation across the whole segment.
    """
    if not tokens:
        return []
    if not char_timings:
        return _interpolate(tokens, seg_start, seg_end)

    spans = _match_spans(tokens, char_timings)
    words = _resolve_spans(tokens, spans, seg_start, seg_end)
    return _enforce_monotonic(words, seg_start, seg_end)


def _match_spans(
    tokens: list[str], char_timings: list[CharTiming]
) -> list[tuple[float, float] | None]:
    """Greedy base-char walk: per token, return (start, end) or None if no char
    matched. Combining marks are skipped on both sides (see _is_combining)."""
    spans: list[tuple[float, float] | None] = []
    idx = 0
    n = len(char_timings)
    for tok in tokens:
        start_t: float | None = None
        end_t: float | None = None
        for ch in tok:
            if ch.isspace() or _is_combining(ch):
                continue
            probe = idx
            while probe < n and char_timings[probe].char != ch:
                probe += 1
            if probe < n:
                idx = probe
                if start_t is None:
                    start_t = char_timings[idx].start
                end_t = char_timings[idx].end
                idx += 1
        spans.append((start_t, end_t) if start_t is not None and end_t is not None else None)
    return spans


def _resolve_spans(
    tokens: list[str],
    spans: list[tuple[float, float] | None],
    seg_start: float,
    seg_end: float,
) -> list[Word]:
    """Turn (start,end)|None spans into Words, filling None runs by spreading the
    time between the previous matched end and the next matched start by length."""
    n = len(tokens)
    words: list[Word | None] = [None] * n
    i = 0
    while i < n:
        span = spans[i]
        if span is not None:
            s, e = span
            words[i] = Word(text=tokens[i], start=round(s, 3), end=round(max(e, s), 3))
            i += 1
            continue
        # Gap run [i, j): consecutive unmatched tokens.
        j = i
        while j < n and spans[j] is None:
            j += 1
        left = words[i - 1].end if i > 0 and words[i - 1] is not None else seg_start
        right = spans[j][0] if j < n else seg_end
        right = max(right, left)
        gap_tokens = tokens[i:j]
        weights = [max(len(t), 1) for t in gap_tokens]
        total = sum(weights)
        avail = max(right - left, 0.0)
        cursor = left
        for k, (t, w) in enumerate(zip(gap_tokens, weights)):
            dur = avail * (w / total)
            # F6: this token had no char match — its span is a guess.
            words[i + k] = Word(
                text=t, start=round(cursor, 3), end=round(cursor + dur, 3),
                interpolated=True,
            )
            cursor += dur
        i = j
    return [w for w in words if w is not None]


def _enforce_monotonic(words: list[Word], seg_start: float, seg_end: float) -> list[Word]:
    """Guarantee non-decreasing, non-overlapping spans (acceptance criterion)."""
    prev_end = seg_start
    for w in words:
        if w.start < prev_end:
            w.start = round(prev_end, 3)
        if w.end < w.start:
            w.end = w.start
        prev_end = w.end
    return words
