#!/usr/bin/env python3
"""M0 validation harness (PRD section 7.7).

POSTs each vocal stem in tests/samples/ to a running service, dumps LRC + word
JSON to tests/out/, and prints a human-readable report. Where a human-made
reference exists on LRCLIB, it also prints NUMBERS (CER/WER + line-timing
offset) so the go/no-go is data-driven, not just eyeballed.

The numbers are a guide, not an automatic pass/fail -- luk-thung accuracy is a
judgement call, and LRCLIB's Thai/luk-thung coverage is thin so many songs will
have no reference (recorded as "no ref"). This is dev/eval tooling only; it does
NOT touch the product flow (so it does not violate the pure-ASR decision, §2).

Usage:
    # 1. start the service:  uvicorn app.main:app --port 8000
    # 2. drop luk-thung vocal .wav files in server/tests/samples/
    # 3. python tests/run_eval.py [--url http://localhost:8000] [--no-lrclib]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).parent
SAMPLES = HERE / "samples"
OUT = HERE / "out"

# Make `app` importable (for PyThaiNLP tokenization used by WER) and the local
# pure-metrics module.
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import eval_metrics as em  # noqa: E402

LRCLIB_BASE = "https://lrclib.net/api"
DEFAULT_UA = "lyricbridge-eval/0.2 (+https://github.com/avocadu14/lyricbridge)"

# LRCLIB asks clients to respect rate limits (PRD section 7.7). Space requests
# out and back off on 429/503 so a burst of lookups never falsely reports a
# real song as "no ref" (which happened to รักโกรธ in the first run).
_LRCLIB_MIN_INTERVAL = 1.0  # seconds between requests
_last_request_at = [0.0]


def _throttled_get(session, url, params, headers, timeout=20, retries=3):
    """GET with a min inter-request gap + exponential backoff on 429/503.

    Returns the response (any status) or None if every attempt errored.
    """
    backoff = 2.0
    for attempt in range(retries):
        gap = _LRCLIB_MIN_INTERVAL - (time.monotonic() - _last_request_at[0])
        if gap > 0:
            time.sleep(gap)
        try:
            r = session.get(url, params=params, headers=headers, timeout=timeout)
        except Exception:
            time.sleep(backoff)
            backoff *= 2
            continue
        finally:
            _last_request_at[0] = time.monotonic()

        if r.status_code in (429, 503) and attempt < retries - 1:
            retry_after = r.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else backoff
            except ValueError:
                wait = backoff
            time.sleep(wait)
            backoff *= 2
            continue
        return r
    return None


def lrclib_lookup(session, artist: str, title: str, duration: float, ua: str):
    """Return an LRCLIB record (dict) or None. Exact /get, then fuzzy /search.

    Filenames in the wild are inconsistently ordered ("Artist - Title" vs
    "Title - Artist"), so we try BOTH orderings before giving up. Never raises --
    any network/parse failure degrades to None so the eval keeps going and
    records the song as having no reference.
    """
    headers = {"User-Agent": ua}
    # Both orderings; drop the empty-artist dupes while preserving order.
    orderings = [(artist, title), (title, artist)]
    seen = set()
    pairs = []
    for a, t in orderings:
        if t and (a, t) not in seen:
            seen.add((a, t))
            pairs.append((a, t))

    # 1) Exact get (duration must be within ~2s or it 404s).
    for a, t in pairs:
        params = {"track_name": t, "artist_name": a}
        if duration:
            params["duration"] = int(round(duration))
        r = _throttled_get(session, f"{LRCLIB_BASE}/get", params, headers)
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                pass

    # 2) Fuzzy search -> pick the candidate closest in duration.
    for a, t in pairs:
        q = {"track_name": t}
        if a:
            q["artist_name"] = a
        r = _throttled_get(session, f"{LRCLIB_BASE}/search", q, headers)
        if r is None or r.status_code != 200:
            continue
        try:
            candidates = r.json() or []
        except Exception:
            continue
        if not candidates:
            continue
        if duration:
            candidates.sort(key=lambda c: abs((c.get("duration") or 0) - duration))
        return candidates[0]
    return None


def score_against_reference(data: dict, ref: dict) -> dict:
    """Compute CER/WER (text) and line-timing offsets vs an LRCLIB record."""
    from app import thai

    hyp_words = [w["text"] for w in data.get("words", [])]
    hyp_text = "".join(hyp_words)
    ref_text = ref.get("plainLyrics") or ""

    # WER over newmm tokens; ref tokenized the same way our pipeline tokenizes.
    ref_tokens = thai.tokenize(ref_text.replace("\n", " "))

    timing = em.TimingReport(0, 0, None, None)
    synced = ref.get("syncedLyrics")
    if synced:
        timing = em.line_timing_offsets(
            em.lrc_line_starts(data.get("lrc", "")),
            em.lrc_line_starts(synced),
        )

    return {
        "ref_track": f"{ref.get('artistName', '')} - {ref.get('trackName', '')}",
        "ref_duration": ref.get("duration"),
        "has_synced": bool(synced),
        "cer": em.cer(ref_text, hyp_text),
        "wer": em.wer(ref_tokens, hyp_words),
        "timing": timing,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--lang", default="th")
    parser.add_argument("--no-lrclib", action="store_true", help="skip ground-truth lookup")
    parser.add_argument("--user-agent", default=DEFAULT_UA)
    parser.add_argument("--samples", default=str(SAMPLES), help="dir of input .wav files")
    parser.add_argument("--out", default=str(OUT), help="dir for LRC/ASS/JSON output")
    args = parser.parse_args()

    import requests  # local import so the file imports without the dep

    samples_dir = pathlib.Path(args.samples)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    wavs = sorted(samples_dir.glob("*.wav"))
    if not wavs:
        print(f"No .wav samples in {samples_dir}. Add luk-thung vocal stems first.")
        return 1

    session = requests.Session()

    print(f"{'song':<28} {'words':>6} {'dur(s)':>8} {'timed%':>7} {'aligned':>8}")
    print("-" * 62)

    scored: list[tuple[str, dict | None]] = []
    for wav in wavs:
        with open(wav, "rb") as fh:
            r = session.post(
                f"{args.url}/transcribe",
                files={"file": (wav.name, fh, "audio/wav")},
                data={"lang": args.lang, "format": "json"},
                timeout=1800,
            )
        if r.status_code != 200:
            print(f"{wav.name:<28}  ERROR {r.status_code}: {r.text[:80]}")
            scored.append((wav.name, None))
            continue

        data = r.json()
        words = data.get("words", [])
        dur = data.get("duration_sec", 0.0)
        timed = sum(1 for w in words if w["end"] > w["start"])
        pct = (100.0 * timed / len(words)) if words else 0.0

        (out_dir / f"{wav.stem}.lrc").write_text(data.get("lrc", ""), encoding="utf-8")
        (out_dir / f"{wav.stem}.ass").write_text(data.get("ass", ""), encoding="utf-8")
        (out_dir / f"{wav.stem}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(
            f"{wav.name:<28} {len(words):>6} {dur:>8.1f} {pct:>6.0f}% "
            f"{str(data.get('aligned')):>8}"
        )

        result = None
        if not args.no_lrclib:
            artist, title = em.parse_song_filename(wav.stem)
            ref = lrclib_lookup(session, artist, title, dur, args.user_agent)
            if ref:
                result = score_against_reference(data, ref)
        scored.append((wav.name, result))

    _print_ground_truth(scored)
    print(f"\nOutputs written to {out_dir}/  — eyeball the .lrc against the song.")
    return 0


def _print_ground_truth(scored: list[tuple[str, dict | None]]) -> None:
    print("\n=== LRCLIB ground truth (PRD 7.7) — lower CER/WER/offset is better ===")
    print("CER/WER from plainLyrics; line offset from syncedLyrics (line-level only).")
    print(f"{'song':<28} {'CER':>6} {'WER':>6} {'lineOff(med/p90)':>18} {'reference':>10}")
    print("-" * 74)
    for name, res in scored:
        short = name[:27]
        if res is None:
            print(f"{short:<28} {'—':>6} {'—':>6} {'—':>18} {'no ref':>10}")
            continue
        t = res["timing"]
        if t.median_offset is None:
            off = "no synced"
        else:
            off = f"{t.median_offset:.2f}/{t.p90_offset:.2f}s"
        print(
            f"{short:<28} {res['cer']*100:>5.0f}% {res['wer']*100:>5.0f}% "
            f"{off:>18} {'found':>10}"
        )
    print(
        "\nNote: word-level timing is NOT validated here (LRCLIB is line-level) —"
        "\nspot-check the per-word highlight manually. Thin luk-thung coverage means"
        "\n'no ref' is expected for many songs; fall back to owner judgement (พอใช้)."
    )


if __name__ == "__main__":
    sys.exit(main())
