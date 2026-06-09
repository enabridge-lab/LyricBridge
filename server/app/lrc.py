"""Build LRC + ASS karaoke files from word spans (PRD section 7.2 step 4).

LRC: one timestamp per line, simplest for the web player (M2).
ASS: \\k centisecond tags per word for color-sweep, burned by ffmpeg (M3).

Lines are reconstructed by grouping words back into the ASR segments they came
from. We carry a per-word `line` index so both formats agree on line breaks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .schemas import Word


# Line-break tuning (PRD M2: the word-by-word highlighter needs short,
# screen-sized lines, not whole verses on one timestamp). Sung-Thai ASR
# segments can run 30-50s; we re-break each at the first of a silence gap, a
# max duration, or a max character width. Env-tunable so the owner can adjust
# without code changes.
LINE_MAX_GAP_SEC = float(os.getenv("LRC_MAX_GAP_SEC", "0.7"))
LINE_MAX_DUR_SEC = float(os.getenv("LRC_MAX_DUR_SEC", "7.0"))
LINE_MAX_CHARS = int(os.getenv("LRC_MAX_CHARS", "30"))


@dataclass
class Line:
    """A karaoke line: words sharing a start timestamp."""

    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return "".join(w.text for w in self.words)


def _fmt_lrc_ts(seconds: float) -> str:
    """[mm:ss.xx] — LRC uses centiseconds."""
    seconds = max(seconds, 0.0)
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:05.2f}"


def _fmt_ass_ts(seconds: float) -> str:
    """h:mm:ss.cc — ASS timestamp (centiseconds)."""
    seconds = max(seconds, 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _split_words(words: list[Word]) -> list[list[Word]]:
    """Break one ASR segment's words into screen-sized karaoke lines.

    Starts a new line before a word when the first of these trips: a silence
    gap from the previous word, the running line duration, or its character
    width would exceed the caps. Guarantees: every word lands in exactly one
    line, order preserved, no empty line. A single word longer than a cap stays
    on its own line (you can't split a word).
    """
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            dur = w.end - cur[0].start
            chars = sum(len(x.text) for x in cur) + len(w.text)
            if gap > LINE_MAX_GAP_SEC or dur > LINE_MAX_DUR_SEC or chars > LINE_MAX_CHARS:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def to_lines(words: list[Word], lines: list[list[Word]] | None = None) -> list[Line]:
    """Group words into Lines, sub-breaking long segments into karaoke lines.

    If explicit segment grouping is given, each segment is the outer boundary
    and `_split_words` re-breaks any verse-length segment into shorter lines.
    Without grouping, each word becomes its own line (degraded/no-align path).
    """
    groups = lines if lines is not None else [[w] for w in words]
    out: list[Line] = []
    for ws in groups:
        if not ws:
            continue
        for chunk in _split_words(ws):
            out.append(Line(chunk))
    return out


def to_lrc(lines: list[Line]) -> str:
    """Render LRC. One [mm:ss.xx] tag per line (line-level scroll)."""
    out = []
    for ln in lines:
        out.append(f"[{_fmt_lrc_ts(ln.start)}]{ln.text}")
    return "\n".join(out)


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Sarabun,48,&H00FFFFFF,&H0000A5FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,1,2,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def to_ass(lines: list[Line]) -> str:
    """Render ASS with per-word \\k karaoke sweep tags (centiseconds).

    The karaoke clock must cover the WHOLE Dialogue span, else the sweep finishes
    early. So before each word we emit an empty `{\\k<gap>}` for the silence since
    the previous word ended; that makes sum(\\k) == End - Start of the line
    (per-word `\\k` only counts sung time and would drift on gappy luk-thung lines).
    """
    body = [_ASS_HEADER]
    for ln in lines:
        parts = []
        prev_end = ln.start  # = words[0].start, but be defensive
        for w in ln.words:
            gap_cs = int(round((w.start - prev_end) * 100))
            if gap_cs > 0:
                parts.append(f"{{\\k{gap_cs}}}")  # empty: holds time, no text
            dur_cs = max(int(round((w.end - w.start) * 100)), 0)
            parts.append(f"{{\\k{dur_cs}}}{w.text}")
            prev_end = w.end
        text = "".join(parts)
        body.append(
            f"Dialogue: 0,{_fmt_ass_ts(ln.start)},{_fmt_ass_ts(ln.end)},"
            f"Default,,0,0,0,,{text}"
        )
    return "\n".join(body) + "\n"
