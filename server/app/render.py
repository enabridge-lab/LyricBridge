"""M3 karaoke video render — ffmpeg burns the ASS \\k sweep over the instrumental.

Takes the instrumental audio (M1) + the ASS subtitles (M0 `lrc.to_ass`) and
produces an `.mp4`: a solid-colour video sized to the ASS canvas, the karaoke
subtitles burned in with libass, muxed with the audio. Stateless work dir.

Font note (PRD Thai rule): the generated ASS declares `Sarabun`, but libass can
only use a font that is actually installed. We force a Thai-capable font via the
`subtitles` filter so Thai never renders as tofu. Default `Noto Sans Thai`
(present on most Linux); set RENDER_FONT=Sarabun once `fonts-tlwg-sarabun` is
installed (the Dockerfile should add it) to match the web player exactly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


RENDER_FONT = os.getenv("RENDER_FONT", "Noto Sans Thai")
RENDER_WIDTH = int(os.getenv("RENDER_WIDTH", "1280"))   # matches ASS PlayResX
RENDER_HEIGHT = int(os.getenv("RENDER_HEIGHT", "720"))  # matches ASS PlayResY
RENDER_BG = os.getenv("RENDER_BG", "black")
RENDER_FPS = int(os.getenv("RENDER_FPS", "24"))
# Video codec. libx264 (CPU) is the portable default; set RENDER_VCODEC=h264_nvenc
# to GPU-encode on an NVENC card (GTX 1650 Turing has it). If the requested
# encoder isn't in this ffmpeg build we fall back to libx264 (see _resolve_vcodec).
RENDER_VCODEC = os.getenv("RENDER_VCODEC", "libx264")

_ENCODER_CACHE: dict[str, bool] = {}


def _ffmpeg_has_encoder(name: str) -> bool:
    """True if this ffmpeg build exposes the given encoder (cached)."""
    if name in _ENCODER_CACHE:
        return _ENCODER_CACHE[name]
    ok = False
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=False,
        ).stdout
        ok = any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())
    except Exception:  # noqa: BLE001 - missing ffmpeg handled later
        ok = False
    _ENCODER_CACHE[name] = ok
    return ok


def _resolve_vcodec() -> str:
    """Use RENDER_VCODEC if this ffmpeg supports it, else fall back to libx264."""
    want = RENDER_VCODEC.strip() or "libx264"
    if want == "libx264" or _ffmpeg_has_encoder(want):
        return want
    import logging

    logging.getLogger(__name__).warning(
        "RENDER_VCODEC=%s not available in this ffmpeg build; using libx264.", want
    )
    return "libx264"


@dataclass(frozen=True)
class RenderResult:
    video_path: Path
    width: int
    height: int
    font: str


def ffmpeg_command(
    audio_path: Path,
    ass_path: Path,
    out_path: Path,
    *,
    duration: float | None = None,
) -> list[str]:
    """Build the ffmpeg burn command. Pure (no IO) so it unit-tests cleanly.

    `ass_path` should be a simple ASCII path (no spaces/colons) — see
    `render_video`, which stages the subtitles as `subs.ass` to dodge libass's
    filter-escaping rules.
    """
    bg = f"color=c={RENDER_BG}:s={RENDER_WIDTH}x{RENDER_HEIGHT}:r={RENDER_FPS}"
    vf = f"subtitles={ass_path}:force_style=FontName={RENDER_FONT}"
    vcodec = _resolve_vcodec()
    # nvenc uses a "preset pN" scale; libx264 uses named presets. yuv420p keeps
    # the output broadly playable on both.
    preset = ["-preset", "p4"] if vcodec.endswith("_nvenc") else ["-preset", "veryfast"]
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", bg,
        "-i", str(audio_path),
        "-vf", vf,
        "-c:v", vcodec, *preset, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
    ]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += [str(out_path)]
    return cmd


def render_video(
    audio_path: str | Path,
    ass: str | Path,
    work_dir: str | Path,
    *,
    out_name: str = "karaoke.mp4",
    duration: float | None = None,
) -> RenderResult:
    """Render a karaoke mp4 from instrumental audio + ASS subtitles.

    `ass` may be the subtitle text or a path to a .ass file. It is staged as
    `<work_dir>/subs.ass` (ASCII name) so the ffmpeg `subtitles` filter needs no
    path escaping. `duration` optionally caps the render (handy for smoke tests).
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    ass_path = work / "subs.ass"
    _stage_ass(ass, ass_path)
    out_path = work / out_name

    cmd = ffmpeg_command(Path(audio_path), ass_path, out_path, duration=duration)
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found; required for video render") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"ffmpeg render failed: {detail[-600:]}") from exc

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no output file")
    return RenderResult(
        video_path=out_path,
        width=RENDER_WIDTH,
        height=RENDER_HEIGHT,
        font=RENDER_FONT,
    )


def _stage_ass(ass: str | Path, dest: Path) -> None:
    """Write subtitle text, or copy a .ass file, to `dest`."""
    candidate = None
    if isinstance(ass, Path):
        candidate = ass
    elif isinstance(ass, str) and "\n" not in ass and ass.lower().endswith(".ass"):
        candidate = Path(ass)
    if candidate is not None and candidate.exists():
        shutil.copyfile(candidate, dest)
    else:
        dest.write_text(str(ass), encoding="utf-8")
