#!/usr/bin/env python3
"""Produce vocal stems from the sample songs for the TRUE M0 gate.

The M0 contract says the cloud receives ONLY the vocal stem (PRD section 3), so
the eval must run on stems, not full mixes. This runs Demucs (separate.py) over
tests/samples/*.wav and writes tests/samples_vocals/<same name>.wav -- keeping
the original filename so LRCLIB lookup still works.

Demucs runs as a subprocess that frees the GPU on exit (no co-residency with
Whisper, per section 5.1). GPU + --segment 7 by default; a song that OOMs on GPU
is retried on CPU (correctness before speed).

Usage:
    SEPARATION_DEVICE=cuda DEMUCS_SEGMENT=7 python tests/separate_samples.py
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from app import separate  # noqa: E402

SAMPLES = HERE / "samples"
VOCALS = HERE / "samples_vocals"


def main() -> int:
    VOCALS.mkdir(exist_ok=True)
    wavs = sorted(SAMPLES.glob("*.wav"))
    if not wavs:
        print(f"No .wav in {SAMPLES}")
        return 1

    print(f"Separating {len(wavs)} songs -> {VOCALS}/ (device={separate.SEPARATION_DEVICE}, "
          f"segment={separate.DEMUCS_SEGMENT})")
    for wav in wavs:
        dest = VOCALS / f"{wav.stem}.wav"
        if dest.exists():
            print(f"  skip (exists): {dest.name}")
            continue
        print(f"  separating: {wav.name}", flush=True)
        if not _separate_to(wav, dest):
            print(f"  FAILED: {wav.name}")
    print("done")
    return 0


def _separate_to(wav: pathlib.Path, dest: pathlib.Path) -> bool:
    """Separate one song; retry on CPU if the GPU pass fails (e.g. OOM)."""
    for device in _device_attempts():
        prev = separate.SEPARATION_DEVICE
        separate.SEPARATION_DEVICE = device
        try:
            with tempfile.TemporaryDirectory(prefix="sep_") as td:
                res = separate.separate(wav, td)
                shutil.copyfile(res.vocals_path, dest)
            print(f"    -> {dest.name}  (device={device})", flush=True)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"    {device} failed: {str(e)[:120]}", flush=True)
        finally:
            separate.SEPARATION_DEVICE = prev
    return False


def _device_attempts() -> list[str]:
    """GPU first if configured, then always a CPU fallback (deduped)."""
    order = [separate._resolve_device(separate.SEPARATION_DEVICE), "cpu"]
    seen, out = set(), []
    for d in order:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


if __name__ == "__main__":
    sys.exit(main())
