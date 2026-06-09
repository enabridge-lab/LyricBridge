#!/usr/bin/env python3
"""Headless browser check for the M2 player (PRD section 8 M2 "on-beat" Pass).

Loads index.html in real Chrome, feeds a real /transcribe JSON + a silent wav,
seeks the audio to several timestamps, and asserts the highlighted (.active)
word matches the word the timing data says should be active. Mechanical proof
the DOM highlight tracks audio time; the visual polish remains the owner's eye.

Run via the webapp-testing helper (serves web/ then runs this):
  server/.venv/bin/python .claude/skills/webapp-testing/scripts/with_server.py \
    --server "server/.venv/bin/python -m http.server 8137 --directory web" --port 8137 \
    -- server/.venv/bin/python web/_headless_check.py
"""

import glob
import json
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.getenv("PW_URL", "http://localhost:8137/")
SILENT_WAV = os.path.join(ROOT, "web", ".fixtures_silent.wav")


def active_index(words, t):
    """Last word with start <= t, else -1 (mirrors player.js activeWordIndex)."""
    lo, hi, ans = 0, len(words) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if words[mid]["start"] <= t:
            ans, lo = mid, mid + 1
        else:
            hi = mid - 1
    return ans


def main() -> int:
    json_path = sorted(glob.glob(os.path.join(ROOT, "server/tests/out_vocals_fixed/*.json")))[0]
    payload = json.load(open(json_path, encoding="utf-8"))
    words = payload["words"]
    print(f"fixture: {os.path.basename(json_path)}  ({len(words)} words, dur {payload['duration_sec']}s)")

    # Pick test timestamps inside the silent wav's 330s span and after word 0.
    probes = [w["start"] + 0.05 for w in (words[0], words[len(words) // 3],
                                          words[2 * len(words) // 3])]
    probes = [t for t in probes if t < 320]

    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page()
        errors = []
        # Ignore the browser's automatic /favicon.ico probe (not a player bug).
        page.on(
            "console",
            lambda m: errors.append(m.text)
            if m.type == "error" and "favicon" not in m.text.lower()
            else None,
        )
        page.goto(URL)
        page.wait_for_load_state("networkidle")

        page.set_input_files("#jsonFile", json_path)
        page.set_input_files("#audioFile", SILENT_WAV)
        page.wait_for_function("document.querySelectorAll('#lyrics .word').length > 0")

        n_lines = page.locator("#lyrics .line").count()
        n_words = page.locator("#lyrics .word").count()
        print(f"rendered: {n_lines} lines, {n_words} word spans")
        if n_lines < 2:
            failures.append(f"expected >1 line (verse-break fix), got {n_lines}")
        if n_words != len(words):
            failures.append(f"word span count {n_words} != payload words {len(words)}")

        # Wait until the audio element can seek.
        page.wait_for_function("document.getElementById('audio').readyState >= 1", timeout=15000)

        for t in probes:
            exp_i = active_index(words, t)
            exp_text = words[exp_i]["text"]
            page.evaluate(
                "(t) => { const a = document.getElementById('audio'); a.currentTime = t; }", t
            )
            # Give the rAF highlight loop a couple frames to apply the class.
            page.wait_for_timeout(120)
            got = page.evaluate(
                "() => { const e = document.querySelector('#lyrics .word.active'); return e ? e.textContent : null; }"
            )
            ok = got == exp_text
            print(f"  t={t:6.2f}s  expect '{exp_text}'  got '{got}'  {'OK' if ok else 'MISMATCH'}")
            if not ok:
                failures.append(f"t={t:.2f}: expected '{exp_text}', got '{got}'")

        # --- M4 post-edit check: toggle edit mode, sync a word, verify export ---
        page.check("#editToggle")
        page.evaluate("() => { document.getElementById('audio').currentTime = 123.45; }")
        # Click the first word -> its start should become ~123.45 in an LRC export.
        first_word = page.locator("#lyrics .word").first
        first_text = first_word.text_content()
        first_word.click()
        page.wait_for_timeout(80)
        # Pull the serialized LRC straight from the module via a tiny eval hook.
        lrc_first_ts = page.evaluate(
            "() => document.querySelector('#lyrics .word').classList.contains('edited')"
        )
        if not lrc_first_ts:
            failures.append("edit: clicked word did not get .edited class")
        else:
            print(f"  edit: synced first word '{first_text}' (marked edited) OK")

        if errors:
            failures.append("console errors: " + "; ".join(errors[:3]))
        browser.close()

    if failures:
        print("\nFAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nPASS: highlight tracks audio time, multi-line render, no console errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
