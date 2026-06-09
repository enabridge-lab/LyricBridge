"""M1 server-side source separation via python-audio-separator (nomadkaraoke, MIT).

Honors the working agreement (CLAUDE.md): reuse the maintained library instead of
a hand-rolled Demucs wrapper. One `Separator` backend covers HTDemucs (PRD default)
and UVR-MDX-NET Karaoke 2 ("keep backing vocals/chorus") behind a model-name env,
and its CPU path always works for self-hosters without a GPU.

The cloud contract still wants exactly two stems — clean `vocals` + `instrumental`.
Some models emit those natively (MDX/roformer); 4-stem Demucs emits
Vocals/Drums/Bass/Other, so we synthesise the instrumental by summing the
non-vocal stems (`_derive_instrumental`). `_classify_stems` and that summing are
pure functions so they unit-test without the heavy library installed.

VRAM discipline (PRD section 5.1, 4 GB GTX 1650): Demucs `segment_size` is capped
from DEMUCS_SEGMENT and `shifts` lowered to 1; Whisper never co-resides because
this runs as its own stage. Set SEPARATION_DEVICE=cpu to hide the GPU entirely.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


# Model filenames are passed straight to audio-separator's load_model().
# Speed/quality presets (set SEPARATION_MODEL to switch, no code change):
#   htdemucs.yaml          -> HTDemucs single model (DEFAULT) -- ~4x faster than
#                             the ft bag on CPU; slightly lower quality. 4-stem.
#   htdemucs_ft.yaml       -> HTDemucs v4 fine-tuned, 4-model ensemble: best
#                             quality, ~4x slower (≈26 min/song CPU). 4-stem.
#   UVR_MDXNET_KARA_2.onnx -> UVR-MDX-NET Karaoke 2: native 2-stem (vocals +
#                             instrumental), fast, keeps backing vocals/chorus.
# Default switched ft -> single htdemucs 2026-06-08 (owner-approved) because the
# ft ensemble was ~95% of per-song time on CPU. Note: M0 eval used htdemucs_ft.
SEPARATION_MODEL = os.getenv("SEPARATION_MODEL", "htdemucs.yaml")
SEPARATION_DEVICE = os.getenv("SEPARATION_DEVICE", os.getenv("ASR_DEVICE", "cpu"))
DEMUCS_SEGMENT = int(os.getenv("DEMUCS_SEGMENT", "7"))
# Where audio-separator caches downloaded model weights (persist across requests).
MODEL_FILE_DIR = os.getenv(
    "SEPARATION_MODEL_DIR",
    str(Path(__file__).resolve().parents[2] / "models" / "audio-separator"),
)

# Containers we must demux with ffmpeg before separation (PRD: extract .mp4 audio).
_VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv", ".ts"}


@dataclass(frozen=True)
class SeparationResult:
    vocals_path: Path
    instrumental_path: Path
    model: str
    device: str


def separate(input_path: str | Path, work_dir: str | Path) -> SeparationResult:
    """Run separation and return stable wav paths for vocals + instrumental.

    Always yields exactly two files (`vocals.wav`, `instrumental.wav`) in
    ``work_dir`` regardless of how many stems the chosen model emits.
    """
    src = Path(input_path)
    base_dir = Path(work_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    stems_dir = base_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)

    device = _resolve_device(SEPARATION_DEVICE)

    # Video containers carry no usable waveform for the separator -> pull audio out.
    audio_src = src
    if src.suffix.lower() in _VIDEO_SUFFIXES:
        audio_src = base_dir / "input_audio.wav"
        _extract_audio(src, audio_src)

    output_files, used_device = _separate_with_oom_fallback(audio_src, stems_dir, device)

    stems = _classify_stems(output_files)
    if "vocals" not in stems:
        raise RuntimeError(
            f"separator produced no vocals stem (got: {[p.name for p in output_files]})"
        )

    vocals = _stable_copy(stems["vocals"], base_dir / "vocals.wav")
    instrumental = _resolve_instrumental(stems, base_dir / "instrumental.wav")

    return SeparationResult(
        vocals_path=vocals,
        instrumental_path=instrumental,
        model=SEPARATION_MODEL,
        device=used_device,
    )


def _separate_with_oom_fallback(audio_src: Path, stems_dir: Path, device: str):
    """Run the separator; on CUDA OOM, clean up and retry ONCE on CPU.

    Returns (output_files, device_actually_used). Keeps a too-large song from
    failing outright on the 4 GB GTX 1650 -- slower CPU beats a 500 (PRD 5.1).
    On CPU we hide the GPU (both backends key off torch.cuda.is_available()).
    """
    from .asr import is_oom_error

    def _run(dev: str) -> list[Path]:
        restore_cuda = _force_cpu_env() if dev == "cpu" else None
        try:
            return _run_separator(audio_src, stems_dir, dev)
        finally:
            if restore_cuda is not None:
                restore_cuda()

    try:
        return _run(device), device
    except Exception as exc:  # noqa: BLE001
        if device != "cpu" and is_oom_error(exc):
            import logging

            logging.getLogger(__name__).warning(
                "Separation OOM on %s; retrying on CPU (slower). %s", device, exc
            )
            _cuda_cleanup()
            return _run("cpu"), "cpu"
        raise


def _cuda_cleanup() -> None:
    """Local proxy to asr.cuda_cleanup (kept import-light)."""
    try:
        from .asr import cuda_cleanup

        cuda_cleanup()
    except Exception:  # noqa: BLE001
        pass


def _run_separator(audio_src: Path, out_dir: Path, device: str) -> list[Path]:
    """Drive audio-separator. Imported lazily so app boot/tests don't need it."""
    from audio_separator.separator import Separator  # heavy import

    separator = Separator(
        output_dir=str(out_dir),
        output_format="WAV",
        model_file_dir=MODEL_FILE_DIR,
        # shifts=1 (vs default 2) roughly halves Demucs peak VRAM/time; segment_size
        # caps the chunk so long songs don't OOM the 4 GB card (PRD 5.1).
        demucs_params={
            "segment_size": DEMUCS_SEGMENT,
            "shifts": 1,
            "overlap": 0.25,
            "segments_enabled": True,
        },
    )
    separator.load_model(model_filename=SEPARATION_MODEL)
    try:
        produced = separator.separate(str(audio_src))
        # separate() returns names relative to output_dir on most versions.
        return [_as_path(out_dir, name) for name in produced]
    finally:
        # VRAM discipline (PRD 5.1, "never co-resident" on the 4 GB GTX 1650):
        # release the Demucs/MDX weights NOW, before this stage returns, so they
        # cannot still be resident when the next stage loads Whisper. The library
        # only empties the *cache* after separate(); the weight tensors stay on
        # the GPU until the model object is dropped, and torch reference cycles
        # mean a plain function-return won't reclaim them deterministically.
        _release_separator(separator)


def _release_separator(separator) -> None:
    """Drop a Separator's loaded weights and reclaim its VRAM deterministically.

    audio-separator keeps the loaded model on ``separator.model_instance`` (whose
    nn.Module, e.g. ``demucs_model_instance``, holds the GPU tensors) and only
    runs ``empty_cache()`` after separate() -- that returns *unused* cached blocks
    but leaves the live weights resident. We move any nn.Module off the GPU, null
    the library's strong refs, then gc + empty_cache so the next stage (Whisper)
    starts from a clean card. Best-effort: cleanup must never fail separation.
    """
    try:
        model_instance = getattr(separator, "model_instance", None)
        if model_instance is not None:
            # Move any torch.nn.Module the architecture holds back to CPU before
            # we drop it (covers demucs_model_instance, model, etc. generically).
            for attr, value in list(vars(model_instance).items()):
                if hasattr(value, "to") and hasattr(value, "parameters"):
                    try:
                        value.to("cpu")
                    except Exception:  # noqa: BLE001
                        pass
                    setattr(model_instance, attr, None)
            separator.model_instance = None
    except Exception:  # noqa: BLE001 -- never let cleanup break a good separation
        pass
    _cuda_cleanup()


def free_model() -> None:
    """Reclaim any GPU memory left by separation (PRD 5.1 symmetry with asr/align).

    ``separate()`` already self-releases via _release_separator before returning,
    so this is a cheap, idempotent safety net the orchestrator can call between
    stages without holding a module-level model reference of its own.
    """
    _cuda_cleanup()


def _as_path(out_dir: Path, name: str) -> Path:
    p = Path(name)
    return p if p.is_absolute() else out_dir / p


# --- pure helpers (unit-testable without the library) ------------------------

# Filenames look like "<song>_(Vocals)_<model>.wav" / "..._(Instrumental)_...".
_STEM_TAGS = {
    "vocals": "vocals",
    "instrumental": "instrumental",
    "drums": "drums",
    "bass": "bass",
    "other": "other",
    "guitar": "guitar",
    "piano": "piano",
}


def _classify_stems(output_files: list[Path]) -> dict[str, Path]:
    """Map each produced stem file to a canonical stem name by its ``(Tag)``.

    Last write wins per stem type; unrecognised files are ignored. Pure: takes
    paths, returns a dict, touches no disk.
    """
    classified: dict[str, Path] = {}
    for path in output_files:
        lower = path.name.lower()
        for tag, canonical in _STEM_TAGS.items():
            if f"({tag})" in lower:
                classified[canonical] = path
                break
    return classified


def _resolve_instrumental(stems: dict[str, Path], dest: Path) -> Path:
    """Return a clean instrumental: native stem if present, else sum the rest."""
    if "instrumental" in stems:
        return _stable_copy(stems["instrumental"], dest)
    backing = [p for name, p in stems.items() if name != "vocals"]
    if not backing:
        raise RuntimeError("cannot build instrumental: no non-vocal stems produced")
    _derive_instrumental(backing, dest)
    return dest


def _derive_instrumental(stem_paths: list[Path], dest: Path) -> Path:
    """Sum several stem wavs into one instrumental wav (Demucs 4-stem path).

    Mixes by adding samples then clipping to [-1, 1] so summed peaks don't wrap.
    """
    import numpy as np
    import soundfile as sf

    mix = None
    samplerate = None
    for path in stem_paths:
        data, sr = sf.read(str(path), always_2d=True, dtype="float32")
        if mix is None:
            mix = data
            samplerate = sr
        else:
            if sr != samplerate:
                raise RuntimeError("stem sample rates differ; cannot sum")
            n = min(len(mix), len(data))
            mix = mix[:n] + data[:n]
    if mix is None:
        raise RuntimeError("no stems to sum into instrumental")
    np.clip(mix, -1.0, 1.0, out=mix)
    sf.write(str(dest), mix, samplerate)
    return dest


# --- device + io plumbing ----------------------------------------------------


def _resolve_device(requested: str) -> str:
    """Resolve this stage's device knob to cpu/cuda.

    Delegates to asr.resolve_device so there's a single source of truth for the
    cuda-only-when-available rule -- and so it honors asr's _FORCE_CPU latch (set
    after a prior-stage OOM), which the old duplicate ignored. We still pass our
    OWN requested device (SEPARATION_DEVICE), not asr's.
    """
    from .asr import resolve_device

    return resolve_device(requested)


def _force_cpu_env():
    """Set CUDA_VISIBLE_DEVICES='' so torch/onnxruntime see no GPU. Returns a
    restore callback that puts the previous value back."""
    key = "CUDA_VISIBLE_DEVICES"
    prev = os.environ.get(key)
    os.environ[key] = ""

    def restore() -> None:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev

    return restore


def _extract_audio(src: Path, dest: Path) -> Path:
    """Demux a video container to wav with ffmpeg (44.1 kHz stereo)."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found; required to read video containers") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"ffmpeg audio extraction failed: {detail}") from exc
    return dest


def _stable_copy(src: Path, dest: Path) -> Path:
    if not src.exists():
        raise RuntimeError(f"missing separated stem: {src.name}")
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    return dest
