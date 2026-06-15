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

import json
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

# F1: stems are AAC-encoded before they enter the job store (a raw stem WAV is
# ~35-50 MB; at 128k AAC it's ~3-4 MB). M4A/AAC because every browser including
# Safari plays it (Opus in CAF/WebM does not). Tune via STEM_BITRATE.
STEM_BITRATE = os.getenv("STEM_BITRATE", "128k")

# O1: allowed background-image inputs. The decoded ffprobe codec must also be in
# this set — extension AND content are checked so a renamed/forged file can't
# reach ffmpeg. Keep to still-image codecs (no animated/video inputs).
_BG_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_BG_IMAGE_CODECS = {"mjpeg", "png", "webp", "bmp"}


def is_valid_background_image(path: str | Path) -> bool:
    """O1: True only if `path` is a real still image (extension + ffprobe codec).

    Defends the ffmpeg input: we never pass a raw user string into the filter
    graph, and we reject anything that isn't a known image codec BEFORE spending
    the render. Returns False (never raises) on any probe failure."""
    p = Path(path)
    if p.suffix.lower() not in _BG_IMAGE_EXT:
        return False
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height",
             "-of", "json", str(p)],
            capture_output=True, text=True, check=False,
        ).stdout
        streams = (json.loads(out or "{}").get("streams") or [])
        if not streams:
            return False
        s = streams[0]
        return s.get("codec_name") in _BG_IMAGE_CODECS and int(s.get("width") or 0) > 0
    except Exception:  # noqa: BLE001 - probe failure -> reject
        return False

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
    font: str | None = None,
    background_image: Path | None = None,
) -> list[str]:
    """Build the ffmpeg burn command. Pure (no IO) so it unit-tests cleanly.

    `ass_path` should be a simple ASCII path (no spaces/colons) — see
    `render_video`, which stages the subtitles as `subs.ass` to dodge libass's
    filter-escaping rules.

    O1: when `background_image` is given, a looped still image (scaled+cropped to
    the ASS canvas) replaces the solid lavfi colour as the video base; the
    karaoke subtitles burn on top. The caller must validate the image first
    (`is_valid_background_image`) — it's a server-staged temp path, never raw
    user input in the filter.
    """
    # F8: a caller-chosen font (validated against an allowlist upstream — this
    # string lands inside an ffmpeg filter) overrides the RENDER_FONT default.
    sub_filter = f"subtitles={ass_path}:force_style=FontName={font or RENDER_FONT}"
    vcodec = _resolve_vcodec()
    # nvenc uses a "preset pN" scale; libx264 uses named presets. yuv420p keeps
    # the output broadly playable on both.
    preset = ["-preset", "p4"] if vcodec.endswith("_nvenc") else ["-preset", "veryfast"]
    cmd = ["ffmpeg", "-y"]
    if background_image is not None:
        # Loop the still image; scale to cover then crop to the exact canvas so
        # any aspect ratio fills the frame without distortion. -shortest ends the
        # (infinite) image stream when the audio finishes.
        cmd += ["-loop", "1", "-framerate", str(RENDER_FPS), "-i", str(background_image)]
        vf = (f"scale={RENDER_WIDTH}:{RENDER_HEIGHT}:force_original_aspect_ratio=increase,"
              f"crop={RENDER_WIDTH}:{RENDER_HEIGHT},{sub_filter}")
    else:
        bg = f"color=c={RENDER_BG}:s={RENDER_WIDTH}x{RENDER_HEIGHT}:r={RENDER_FPS}"
        cmd += ["-f", "lavfi", "-i", bg]
        vf = sub_filter
    cmd += [
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
    font: str | None = None,
    background_image: str | Path | None = None,
) -> RenderResult:
    """Render a karaoke mp4 from instrumental audio + ASS subtitles.

    `ass` may be the subtitle text or a path to a .ass file. It is staged as
    `<work_dir>/subs.ass` (ASCII name) so the ffmpeg `subtitles` filter needs no
    path escaping. `duration` optionally caps the render (handy for smoke tests).
    O1: `background_image` (already validated by the caller) replaces the solid
    background — rejected here too as a defence in depth.
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    ass_path = work / "subs.ass"
    _stage_ass(ass, ass_path)
    out_path = work / out_name

    bg_img = None
    if background_image is not None:
        if not is_valid_background_image(background_image):
            raise RuntimeError("invalid background image (not a supported still image)")
        bg_img = Path(background_image)

    cmd = ffmpeg_command(Path(audio_path), ass_path, out_path,
                         duration=duration, font=font, background_image=bg_img)
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


def encode_stem(src_wav: Path, dest: Path, bitrate: str | None = None) -> Path:
    """Encode a stem WAV to AAC (dest extension decides the container, .m4a).

    Raises RuntimeError on any ffmpeg failure — the caller decides whether to
    fall back to serving the WAV (main._encode_stem_or_wav does exactly that).
    NOTE: AAC adds ~20-50 ms of encoder delay; the player's sync-offset slider
    absorbs it, so we don't compensate here.
    """
    bitrate = bitrate or STEM_BITRATE
    cmd = [
        "ffmpeg", "-y", "-i", str(src_wav),
        "-c:a", "aac", "-b:a", bitrate,
        "-movflags", "+faststart",  # moov atom up front -> browser can seek early
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found; required for stem encode") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"ffmpeg stem encode failed: {detail[-300:]}") from exc
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no stem output")
    return dest


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
