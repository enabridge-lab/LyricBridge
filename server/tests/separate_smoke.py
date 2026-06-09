#!/usr/bin/env python3
"""M1 acceptance smoke test: real separation end-to-end.

PRD section 8 M1 Pass = "upload song -> get clean instrumental + vocals stems".
Unit tests cover the pure stem-selection/derivation logic; this drives the actual
python-audio-separator backend on a real song and asserts two non-trivial stems
come out. CPU by default (the self-host promise, PRD section 5); set
SEPARATION_DEVICE=cuda to use the GTX 1650.

Usage:
    cd server
    SEPARATION_DEVICE=cpu .venv/bin/python tests/separate_smoke.py
    # optional: SEPARATION_MODEL=UVR_MDXNET_KARA_2.onnx to spot-check the Karaoke model
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from app import separate  # noqa: E402

SAMPLES = HERE / "samples"


def _smallest_sample() -> pathlib.Path | None:
    wavs = sorted(SAMPLES.glob("*.wav"), key=lambda p: p.stat().st_size)
    return wavs[0] if wavs else None


def main() -> int:
    song = _smallest_sample()
    if song is None:
        print(f"FAIL: no sample .wav in {SAMPLES}")
        return 1

    print(f"model={separate.SEPARATION_MODEL} device={separate.SEPARATION_DEVICE}")
    print(f"separating: {song.name}  ({song.stat().st_size/1e6:.1f} MB)", flush=True)

    with tempfile.TemporaryDirectory(prefix="sep_smoke_") as td:
        try:
            res = separate.separate(song, td)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL: separation raised: {e}")
            return 1

        ok = True
        for label, path in (("vocals", res.vocals_path), ("instrumental", res.instrumental_path)):
            if not path.exists():
                print(f"FAIL: {label} stem missing: {path}")
                ok = False
                continue
            dur, sr = _wav_stats(path)
            size = path.stat().st_size
            print(f"  {label:12s} {size/1e6:6.2f} MB  {dur:6.1f}s @ {sr} Hz  {path.name}")
            # A real stem is not a fraction of a second and not an empty file.
            if dur < 5.0 or size < 100_000:
                print(f"FAIL: {label} stem looks trivial (dur={dur:.2f}s size={size}B)")
                ok = False

        if ok:
            print("PASS: clean vocals + instrumental stems produced (M1 acceptance).")
            return 0
        return 1


def _wav_stats(path: pathlib.Path) -> tuple[float, int]:
    import soundfile as sf

    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate), int(info.samplerate)


if __name__ == "__main__":
    sys.exit(main())
