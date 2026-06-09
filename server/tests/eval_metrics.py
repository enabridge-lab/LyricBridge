"""Pure metric helpers for the M0 LRCLIB ground-truth eval (PRD section 7.7).

No network, no models -- just text/timing math so it can be unit-tested fast.
The eval compares our ASR output against human-made LRCLIB references:
  - TEXT  : CER (and WER) of ASR transcript vs LRCLIB plainLyrics.
  - TIMING: line-start offset of our LRC vs LRCLIB syncedLyrics (line-level).
LRCLIB syncedLyrics is LINE-level only -- it cannot validate per-word timing
(see PRD section 7.7), so word timing still needs manual spot-checks.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# "Artist - Title [youtube_id]" -> ("Artist", "Title"). The trailing [..] (and
# any (..) qualifier) is YouTube/upload noise, not part of the song metadata.
_FILENAME_RE = re.compile(r"^\s*(?P<artist>.+?)\s*-\s*(?P<title>.+?)\s*$")
_BRACKET_RE = re.compile(r"[\[(（【].*?[\])）】]")
_LRC_TS_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")


def parse_song_filename(stem: str) -> tuple[str, str]:
    """Best-effort split of 'Artist - Title [id]' into (artist, title).

    Falls back to ('', whole-stem) when there is no ' - ' separator, so the
    caller can still try a fuzzy LRCLIB search on the raw title.
    """
    cleaned = _BRACKET_RE.sub("", stem).strip()
    # Only treat the FIRST ' - ' as the artist/title boundary (titles may dash).
    if " - " in cleaned:
        artist, title = cleaned.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", cleaned


def normalize_th(text: str) -> str:
    """Collapse text to a comparable Thai character stream for CER.

    Thai has no inter-word spaces and ASR vs reference disagree on line breaks
    and punctuation, so those must not count as errors. We: NFC-normalize, drop
    all whitespace, drop punctuation/symbols, and lowercase Latin. Thai letters,
    tone marks, and digits are kept (tone marks are meaningful in Thai).
    """
    text = unicodedata.normalize("NFC", text)
    out = []
    for ch in text:
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)  # P*=punct, S*=symbol, C*=control
        if cat[0] in ("P", "S", "C"):
            continue
        out.append(ch.lower())
    return "".join(out)


def _levenshtein(ref: list, hyp: list) -> int:
    """Edit distance over two sequences (works for chars or tokens)."""
    if not ref:
        return len(hyp)
    if not hyp:
        return len(ref)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cost = 0 if r == h else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate on normalized Thai text (fairer than WER for Thai).

    Returns edit_distance / len(reference). 0.0 == perfect; can exceed 1.0 when
    the hypothesis is much longer/wronger than the reference.
    """
    ref = normalize_th(reference)
    hyp = normalize_th(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(list(ref), list(hyp)) / len(ref)


def wer(ref_tokens: list[str], hyp_tokens: list[str]) -> float:
    """Word Error Rate over pre-tokenized word lists (tokenize with PyThaiNLP).

    Kept dependency-free by taking already-tokenized input -- run_eval supplies
    newmm tokens. CER is the headline metric for Thai; WER is a secondary view.
    """
    if not ref_tokens:
        return 0.0 if not hyp_tokens else 1.0
    return _levenshtein(ref_tokens, hyp_tokens) / len(ref_tokens)


def lrc_line_starts(lrc_or_synced: str) -> list[float]:
    """Extract [mm:ss.xx] line-start times (seconds), sorted ascending.

    Works for both our LRC output and LRCLIB syncedLyrics (same timestamp form).
    """
    starts: list[float] = []
    for m in _LRC_TS_RE.finditer(lrc_or_synced):
        minutes = int(m.group(1))
        seconds = int(m.group(2))
        frac = m.group(3) or "0"
        frac_sec = int(frac) / (10 ** len(frac))
        starts.append(minutes * 60 + seconds + frac_sec)
    return sorted(starts)


@dataclass
class TimingReport:
    n_hyp_lines: int
    n_ref_lines: int
    median_offset: float | None
    p90_offset: float | None


def line_timing_offsets(
    hyp_starts: list[float], ref_starts: list[float]
) -> TimingReport:
    """For each hypothesis line start, distance to the NEAREST reference start.

    Reports median + p90 of those distances (seconds). Nearest-match (not index
    pairing) because ASR and reference rarely have the same line count -- this
    measures "how far off is our timing", not a strict 1:1 line alignment.
    """
    if not hyp_starts or not ref_starts:
        return TimingReport(len(hyp_starts), len(ref_starts), None, None)
    diffs = sorted(min(abs(h - r) for r in ref_starts) for h in hyp_starts)
    return TimingReport(
        n_hyp_lines=len(hyp_starts),
        n_ref_lines=len(ref_starts),
        median_offset=_percentile(diffs, 50),
        p90_offset=_percentile(diffs, 90),
    )


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (rank - lo)
