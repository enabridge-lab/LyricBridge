"""Fast unit tests for the pure pipeline logic (no models, no GPU).

These cover the parts that don't need faster-whisper/whisperx downloads:
tokenization, char->word timing mapping, monotonicity, and LRC/ASS building.
Run: cd server && python -m pytest tests/test_pipeline_units.py -q
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import thai  # noqa: E402
from app.lrc import to_ass, to_lines, to_lrc  # noqa: E402
from app.schemas import Word  # noqa: E402
from app.thai import CharTiming  # noqa: E402


def test_asr_transcribe_honors_vad_filter_setting(monkeypatch, tmp_path):
    from app import asr

    captured = {}

    class FakeWhisperModel:
        def transcribe(self, wav_path, **kwargs):
            captured.update(kwargs)
            segment = type("Segment", (), {"text": "ฉัน", "start": 0.0, "end": 1.0})
            return iter([segment]), object()

    monkeypatch.setattr(asr, "VAD_FILTER", False)
    monkeypatch.setattr(asr, "_load_model", lambda: (FakeWhisperModel(), "cpu"))

    segments = asr.transcribe(str(tmp_path / "vocal.wav"), lang="th")

    assert len(segments) == 1
    assert captured["vad_filter"] is False


def test_longest_repeat_run_finds_loops_and_ignores_clean_text():
    from app import asr

    # Hallucinated melisma loop: one tiny unit repeated dozens of times.
    unit, count, start, end = asr._longest_repeat_run("จื๊ด" * 60)
    assert (unit, count) == ("จื๊ด", 60)
    assert ("จื๊ด" * 60)[start:end] == "จื๊ด" * count  # span is exact
    # A real sung hook repeated a few times is still detected (policy keeps it).
    assert asr._longest_repeat_run("รักเธอ" * 5)[:2] == ("รักเธอ", 5)
    # Clean lyric text -> nothing repeats.
    assert asr._longest_repeat_run("สวัสดีครับ") is None


def test_repeat_keep_policy_kills_loops_but_spares_real_choruses():
    from app import asr

    # จื๊ด: 2 base chars (combining marks don't count) x60 -> tiny loop -> keep 1.
    assert asr._base_char_len("จื๊ด") == 2
    assert asr._repeat_keep_policy("จื๊ด", 60) == 1
    # รักเธอ: 5 base chars x5 -> plausible chorus -> keep all.
    assert asr._base_char_len("รักเธอ") == 5
    assert asr._repeat_keep_policy("รักเธอ", 5) is None
    # Even a whole phrase repeated 8x is past real -> collapse to 2.
    assert asr._repeat_keep_policy("รักเธอ", 8) == 2


def test_collapse_repeats_rewrites_segment_text():
    from app import asr

    # The actual eval failure: "จื๊ด"x60 collapses to a single hint.
    assert asr.collapse_repeats("จื๊ด" * 60) == "จื๊ด"
    # A real chorus passes through untouched.
    assert asr.collapse_repeats("รักเธอ" * 5) == "รักเธอ" * 5
    # A loop embedded mid-line keeps the surrounding lyric intact.
    assert asr.collapse_repeats("นำหน้า" + "จื๊ด" * 40 + "ตามหลัง") == "นำหน้าจื๊ดตามหลัง"


def test_classify_stems_maps_tags_by_filename():
    from app import separate

    files = [
        pathlib.Path("song_(Vocals)_htdemucs_ft.wav"),
        pathlib.Path("song_(Drums)_htdemucs_ft.wav"),
        pathlib.Path("song_(Bass)_htdemucs_ft.wav"),
        pathlib.Path("song_(Other)_htdemucs_ft.wav"),
        pathlib.Path("readme.txt"),  # ignored: no recognised tag
    ]
    stems = separate._classify_stems(files)

    assert set(stems) == {"vocals", "drums", "bass", "other"}
    assert stems["vocals"].name == "song_(Vocals)_htdemucs_ft.wav"


def test_classify_stems_picks_native_instrumental_from_mdx():
    from app import separate

    files = [
        pathlib.Path("song_(Vocals)_UVR_MDXNET_KARA_2.wav"),
        pathlib.Path("song_(Instrumental)_UVR_MDXNET_KARA_2.wav"),
    ]
    stems = separate._classify_stems(files)
    assert set(stems) == {"vocals", "instrumental"}


def _write_wav(path, samplerate, data):
    import numpy as np
    import soundfile as sf

    sf.write(str(path), np.asarray(data, dtype="float32"), samplerate)


def test_derive_instrumental_sums_stems_and_clips(tmp_path):
    import numpy as np
    import soundfile as sf

    from app import separate

    sr = 8000
    # Three stems that sum past 1.0 on the first sample -> must clip, not wrap.
    _write_wav(tmp_path / "drums.wav", sr, [[0.6], [0.1], [-0.2]])
    _write_wav(tmp_path / "bass.wav", sr, [[0.6], [0.1], [-0.2]])
    _write_wav(tmp_path / "other.wav", sr, [[0.6], [0.1], [-0.2]])

    dest = tmp_path / "instrumental.wav"
    separate._derive_instrumental(
        [tmp_path / "drums.wav", tmp_path / "bass.wav", tmp_path / "other.wav"], dest
    )

    mix, out_sr = sf.read(str(dest), always_2d=True, dtype="float32")
    assert out_sr == sr
    # Tolerances cover int16 WAV quantization (~3e-5), not logic slack.
    assert mix[0, 0] > 0.999  # 1.8 clipped to ~1.0, NOT wrapped negative
    assert abs(mix[1, 0] - 0.3) < 1e-3  # 0.3 sums cleanly
    assert abs(mix[2, 0] - (-0.6)) < 1e-3


def test_separate_yields_two_stems_for_four_stem_model(tmp_path, monkeypatch):
    """End-to-end shape: a 4-stem Demucs run still returns vocals + instrumental."""
    from app import separate

    sr = 8000
    stem_dir = tmp_path / "stems"
    stem_dir.mkdir()
    _write_wav(stem_dir / "s_(Vocals)_m.wav", sr, [[0.5], [0.5]])
    _write_wav(stem_dir / "s_(Drums)_m.wav", sr, [[0.1], [0.1]])
    _write_wav(stem_dir / "s_(Bass)_m.wav", sr, [[0.1], [0.1]])
    _write_wav(stem_dir / "s_(Other)_m.wav", sr, [[0.1], [0.1]])

    def fake_run(audio_src, out_dir, device):
        return [
            stem_dir / "s_(Vocals)_m.wav",
            stem_dir / "s_(Drums)_m.wav",
            stem_dir / "s_(Bass)_m.wav",
            stem_dir / "s_(Other)_m.wav",
        ]

    monkeypatch.setattr(separate, "_run_separator", fake_run)

    result = separate.separate(tmp_path / "song.wav", tmp_path)

    assert result.vocals_path == tmp_path / "vocals.wav"
    assert result.instrumental_path == tmp_path / "instrumental.wav"
    assert result.vocals_path.exists() and result.instrumental_path.exists()
    assert result.model == separate.SEPARATION_MODEL


def test_render_ffmpeg_command_burns_subtitles_with_audio(tmp_path):
    from app import render

    cmd = render.ffmpeg_command(
        tmp_path / "audio.wav", tmp_path / "subs.ass", tmp_path / "out.mp4"
    )
    assert cmd[0] == "ffmpeg"
    # lavfi colour background sized to the ASS canvas
    assert any(a.startswith("color=") and "1280x720" in a for a in cmd)
    # subtitles filter forces a Thai-capable font so Thai never renders as tofu
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("subtitles=") and "force_style=FontName=" in vf
    assert "libx264" in cmd and "aac" in cmd
    assert "-shortest" in cmd
    assert cmd[-1].endswith("out.mp4")
    assert "-t" not in cmd  # no duration cap unless asked


def test_render_ffmpeg_command_caps_duration_when_given(tmp_path):
    from app import render

    cmd = render.ffmpeg_command(
        tmp_path / "a.wav", tmp_path / "s.ass", tmp_path / "o.mp4", duration=15
    )
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "15"


def test_resolve_instrumental_skips_summing_for_2stem_models(tmp_path, monkeypatch):
    # §2: a native 2-stem model (instrumental present) must NOT run _derive (sum).
    from app import separate

    inst = tmp_path / "song_(Instrumental)_mdx.wav"
    inst.write_bytes(b"native instrumental")
    stems = {"vocals": tmp_path / "v.wav", "instrumental": inst}

    def boom(*a, **k):
        raise AssertionError("_derive_instrumental must not run for 2-stem models")

    monkeypatch.setattr(separate, "_derive_instrumental", boom)
    out = separate._resolve_instrumental(stems, tmp_path / "instrumental.wav")
    assert out.read_bytes() == b"native instrumental"


def test_segment_confidence_maps_logprobs_to_0_1():
    # F3: pure mapping from faster-whisper segment scores to a 0..1 confidence.
    from app import asr

    high = asr.segment_confidence(-0.2, 0.05)   # confident decode, clear speech
    low = asr.segment_confidence(-1.5, 0.05)    # shaky decode (melisma-style)
    assert high is not None and low is not None
    assert high > 0.7
    assert low < 0.3
    assert high > low

    # decoder thought it probably wasn't speech at all -> confidence collapses
    suspect = asr.segment_confidence(-0.2, 0.9)
    assert suspect < 0.15

    # no data -> None (old callers / models that don't expose the scores)
    assert asr.segment_confidence(None, None) is None
    assert asr.segment_confidence(None, 0.1) is None

    # clamped: positive logprob can't exceed 1.0; result never negative
    assert asr.segment_confidence(0.5, None) == 1.0
    assert 0.0 <= asr.segment_confidence(-10.0, 1.0) <= 1.0


def test_is_oom_error_detects_cuda_oom():
    from app import asr

    assert asr.is_oom_error(RuntimeError("CUDA out of memory. Tried to allocate..."))
    assert asr.is_oom_error(type("OutOfMemoryError", (Exception,), {})())
    assert not asr.is_oom_error(ValueError("bad audio"))


def test_separate_oom_fallback_retries_on_cpu(tmp_path, monkeypatch):
    # §1: a CUDA OOM on the GPU pass retries once on CPU and reports device=cpu.
    from app import separate

    sr = 8000
    stem_dir = tmp_path / "stems"
    stem_dir.mkdir()
    _write_wav(stem_dir / "s_(Vocals)_m.wav", sr, [[0.5]])
    _write_wav(stem_dir / "s_(Instrumental)_m.wav", sr, [[0.2]])
    calls = []

    def fake_run(audio_src, out_dir, device):
        calls.append(device)
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return [stem_dir / "s_(Vocals)_m.wav", stem_dir / "s_(Instrumental)_m.wav"]

    monkeypatch.setattr(separate, "_run_separator", fake_run)
    monkeypatch.setattr(separate, "_resolve_device", lambda req: "cuda")
    monkeypatch.setattr(separate, "_force_cpu_env", lambda: (lambda: None))

    result = separate.separate(tmp_path / "song.wav", tmp_path)
    assert calls == ["cuda", "cpu"]  # tried GPU, fell back to CPU
    assert result.device == "cpu"


def test_render_vcodec_falls_back_to_libx264_when_unavailable(tmp_path, monkeypatch):
    # §4: requesting an unavailable encoder must degrade to libx264, not fail.
    from app import render

    monkeypatch.setattr(render, "RENDER_VCODEC", "h264_nvenc")
    monkeypatch.setattr(render, "_ffmpeg_has_encoder", lambda name: False)
    cmd = render.ffmpeg_command(tmp_path / "a.wav", tmp_path / "s.ass", tmp_path / "o.mp4")
    assert "libx264" in cmd and "h264_nvenc" not in cmd


def test_render_vcodec_uses_nvenc_when_available(tmp_path, monkeypatch):
    from app import render

    monkeypatch.setattr(render, "RENDER_VCODEC", "h264_nvenc")
    monkeypatch.setattr(render, "_ffmpeg_has_encoder", lambda name: True)
    cmd = render.ffmpeg_command(tmp_path / "a.wav", tmp_path / "s.ass", tmp_path / "o.mp4")
    assert "h264_nvenc" in cmd
    assert cmd[cmd.index("-preset") + 1] == "p4"  # nvenc preset scale


def test_encode_stem_produces_smaller_playable_m4a(tmp_path):
    # F1: a real (tiny) WAV encodes to an m4a that ffprobe identifies as AAC
    # and that is smaller than the source. Uses the system ffmpeg like prod.
    import shutil as _sh
    import subprocess

    import numpy as np

    import pytest

    from app import render

    if not _sh.which("ffmpeg") or not _sh.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not installed")

    src = tmp_path / "stem.wav"
    sr = 44100
    t = np.linspace(0, 2.0, sr * 2, endpoint=False)
    _write_wav(src, sr, (0.3 * np.sin(2 * np.pi * 440 * t)).reshape(-1, 1))

    dest = render.encode_stem(src, tmp_path / "stem.m4a")
    assert dest.exists() and dest.stat().st_size > 0
    assert dest.stat().st_size < src.stat().st_size  # compressed

    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(dest)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert codec == "aac"


def test_encode_stem_raises_on_bad_input(tmp_path):
    # Caller (main._encode_stem_or_wav) relies on a raised error to fall back.
    import shutil as _sh

    import pytest

    from app import render

    if not _sh.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")

    bad = tmp_path / "not_audio.wav"
    bad.write_bytes(b"this is not a wav file")
    with pytest.raises(RuntimeError):
        render.encode_stem(bad, tmp_path / "out.m4a")


def test_render_stage_ass_writes_text_and_copies_file(tmp_path):
    from app import render

    # text payload -> written verbatim
    dest = tmp_path / "subs.ass"
    render._stage_ass("[Events]\nDialogue: hi", dest)
    assert "Dialogue: hi" in dest.read_text(encoding="utf-8")

    # path payload -> copied
    src = tmp_path / "src.ass"
    src.write_text("from-file", encoding="utf-8")
    dest2 = tmp_path / "subs2.ass"
    render._stage_ass(src, dest2)
    assert dest2.read_text(encoding="utf-8") == "from-file"


def test_tokenize_splits_thai_words():
    toks = thai.tokenize("ฉันคิดถึงเธอ")
    assert len(toks) >= 2  # not one blob, not per-character
    assert "".join(toks) == "ฉันคิดถึงเธอ"


def test_interpolation_is_monotonic_without_alignment():
    words = thai.map_words(["ฉัน", "คิดถึง", "เธอ"], 10.0, 13.0, None)
    assert len(words) == 3
    assert words[0].start == 10.0
    for a, b in zip(words, words[1:]):
        assert a.end <= b.start  # non-overlapping
        assert a.start <= a.end


def test_map_words_uses_char_timings():
    text = "ฉันคิด"
    base = 5.0
    chars = [CharTiming(c, base + i * 0.1, base + i * 0.1 + 0.1) for i, c in enumerate(text)]
    words = thai.map_words(["ฉัน", "คิด"], 5.0, 6.0, chars)
    assert words[0].text == "ฉัน"
    assert abs(words[0].start - 5.0) < 1e-6
    assert words[1].start >= words[0].end  # second word starts after first


def test_map_words_interpolates_unmatched_token_between_neighbours():
    # §4: a middle token with NO char timing must get a real span spread between
    # its matched neighbours, not collapse to a zero-width point.
    chars = (
        [CharTiming("ก", 0.0, 1.0)]
        + [CharTiming("ค", 3.0, 4.0)]  # 'ข' has no timing at all
    )
    words = thai.map_words(["ก", "ข", "ค"], 0.0, 5.0, chars)
    assert [w.text for w in words] == ["ก", "ข", "ค"]
    mid = words[1]
    assert mid.end > mid.start  # NOT a point
    assert mid.start >= words[0].end and mid.end <= words[2].start  # between
    # monotonic, non-overlapping preserved
    for a, b in zip(words, words[1:]):
        assert a.end <= b.start


def test_map_words_trailing_unmatched_run_spreads_to_segment_end():
    chars = [CharTiming("ก", 0.0, 1.0)]
    words = thai.map_words(["ก", "ข", "ค"], 0.0, 5.0, chars)
    # ข and ค unmatched -> spread across (1.0, 5.0), increasing, not collapsed
    assert words[1].end > words[1].start
    assert words[2].end > words[2].start
    assert words[1].start >= words[0].end
    assert words[2].end <= 5.0 + 1e-6


def test_map_words_skips_combining_marks_when_matching():
    # WhisperX omitted the tone mark '้' from the stream; base chars still match.
    chars = [CharTiming("ร", 0.0, 0.5), CharTiming("ก", 0.5, 1.0)]  # no '้'
    words = thai.map_words(["รัก"], 0.0, 1.0, chars)  # 'รัก' = ร + ั + ก
    assert words[0].text == "รัก"
    assert abs(words[0].start - 0.0) < 1e-6
    assert words[0].end >= words[0].start


def test_map_words_flags_interpolated_per_word():
    # F6: matched tokens keep interpolated=False; the unmatched middle token
    # (its span is spread between neighbours) is flagged True.
    chars = [CharTiming("ก", 0.0, 1.0)] + [CharTiming("ค", 3.0, 4.0)]  # 'ข' missing
    words = thai.map_words(["ก", "ข", "ค"], 0.0, 5.0, chars)
    assert [w.interpolated for w in words] == [False, True, False]


def test_map_words_without_chars_flags_all_interpolated():
    # F6: no char timings at all -> the whole segment's timing is guessed.
    words = thai.map_words(["ฉัน", "คิดถึง", "เธอ"], 10.0, 13.0, None)
    assert all(w.interpolated for w in words)


def test_enforce_monotonic_preserves_interpolated_flags():
    # F6: the monotonic pass mutates times in place — flags must survive.
    words = [
        Word(text="ก", start=0.0, end=1.0, interpolated=False),
        Word(text="ข", start=0.5, end=0.4, interpolated=True),  # overlaps -> fixed
    ]
    out = thai._enforce_monotonic(words, 0.0, 2.0)
    assert [w.interpolated for w in out] == [False, True]
    assert out[1].start >= out[0].end  # and it still did its job


def test_romanize_word_returns_reading_and_degrades_safely(monkeypatch):
    # F7: royin gives a non-empty reading; a broken engine -> "" (never raises).
    # NOTE: pythainlp silently falls back on unknown engine names, so the
    # failure path is exercised by breaking the romanize function itself.
    assert thai.romanize_word("รัก") != ""

    import importlib

    # pythainlp shadows the submodule with a same-named function at package
    # level, so reach the real module via importlib.
    tl = importlib.import_module("pythainlp.transliterate")

    def boom(*a, **k):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(tl, "romanize", boom)
    monkeypatch.setattr(thai, "_romanize_warned", False)
    assert thai.romanize_word("รัก") == ""  # degraded, no exception


def test_split_windows_short_segment_is_single_window():
    from app import align

    text = "สวัสดีครับ"
    windows = align._split_windows(0.0, 5.0, text, 20.0)
    assert windows == [(0.0, 5.0, text)]


def test_split_windows_long_segment_spans_without_gaps_or_lost_text():
    from app import align

    text = "abcdefghij"
    start, end = 10.0, 60.0  # 50s, max 20s -> 3 windows
    windows = align._split_windows(start, end, text, 20.0)

    assert len(windows) == 3
    # 1) spans the whole segment exactly
    assert windows[0][0] == start
    assert windows[-1][1] == end
    # 2) no gaps/overlaps -> stitched char timings stay monotonic
    for a, b in zip(windows, windows[1:]):
        assert abs(a[1] - b[0]) < 1e-9
        assert a[1] <= b[1]
    # 3) every character lands in exactly one window (none lost or duplicated)
    assert "".join(t for _, _, t in windows) == text
    # 4) no window exceeds the cap
    for w_start, w_end, _ in windows:
        assert w_end - w_start <= 20.0 + 1e-9


def test_parse_song_filename_strips_youtube_id():
    import eval_metrics as em

    assert em.parse_song_filename("Bodyslam - ความรัก [XZp9BOjvD7o]") == (
        "Bodyslam",
        "ความรัก",
    )
    # No separator -> empty artist, whole thing as title (for fuzzy search).
    assert em.parse_song_filename("เพลงไม่มีศิลปิน") == ("", "เพลงไม่มีศิลปิน")


def test_cer_ignores_whitespace_and_punctuation():
    import eval_metrics as em

    # Identical Thai text, differing only in spaces/newlines/punct -> 0 errors.
    assert em.cer("ฉันรักเธอ นะ!", "ฉัน\nรักเธอนะ") == 0.0
    # One substituted char out of 9 reference chars.
    assert abs(em.cer("ฉันรักเธอมากมาย", "ฉันรักเธอมากมาก") - (1 / 15)) < 1e-9


def test_wer_on_token_lists():
    import eval_metrics as em

    assert em.wer(["ฉัน", "รัก", "เธอ"], ["ฉัน", "รัก", "เธอ"]) == 0.0
    # One wrong token of three.
    assert abs(em.wer(["ฉัน", "รัก", "เธอ"], ["ฉัน", "รัก", "เขา"]) - (1 / 3)) < 1e-9


def test_lrc_line_starts_parses_timestamps():
    import eval_metrics as em

    starts = em.lrc_line_starts("[00:05.32]a\n[01:10.50]b\n[00:01.00]c")
    assert starts == [1.0, 5.32, 70.5]  # sorted ascending


def test_line_timing_offsets_nearest_match():
    import eval_metrics as em

    # hyp lines 0.0, 10.0 vs ref 0.5, 9.0 -> offsets 0.5 and 1.0
    rep = em.line_timing_offsets([0.0, 10.0], [0.5, 9.0])
    assert rep.n_hyp_lines == 2 and rep.n_ref_lines == 2
    assert abs(rep.median_offset - 0.75) < 1e-9  # mean of [0.5, 1.0] at p50
    # Empty reference -> no offsets, reported as None (not a crash).
    empty = em.line_timing_offsets([1.0], [])
    assert empty.median_offset is None


def test_hex_to_ass_colour_swaps_to_bgr():
    # F8: ASS colours are little-endian BGR — the classic byte-order trap.
    from app.lrc import _hex_to_ass_colour

    assert _hex_to_ass_colour("FFA500") == "&H0000A5FF"  # web orange
    assert _hex_to_ass_colour("112233") == "&H00332211"
    assert _hex_to_ass_colour("ffffff") == "&H00FFFFFF"  # lowercase ok
    import pytest

    for bad in ("FFF", "GGGGGG", "#FFFFFF", "", "FFFFFF00"):
        with pytest.raises(ValueError):
            _hex_to_ass_colour(bad)


def test_default_ass_style_reproduces_historic_header_exactly():
    # F8: default AssStyle must not change existing output by a single byte.
    from app.lrc import AssStyle, _ass_header

    legacy = """[Script Info]
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
    assert _ass_header(AssStyle()) == legacy


def test_ass_style_rejects_out_of_range_values():
    from app.lrc import AssStyle

    import pytest

    with pytest.raises(ValueError):
        AssStyle(font_size=300)
    with pytest.raises(ValueError):
        AssStyle(alignment=0)
    with pytest.raises(ValueError):
        AssStyle(primary_colour="not-hex")
    with pytest.raises(ValueError):
        AssStyle(font="Sarabun,Injection")
    with pytest.raises(ValueError):
        AssStyle(margin_v=9999)


def test_to_ass_applies_custom_style():
    from app.lrc import AssStyle

    words = [Word(text="ก", start=0.0, end=1.0)]
    lines = to_lines([*words], [words])
    ass = to_ass(lines, AssStyle(font="Noto Sans Thai", font_size=64,
                                 primary_colour="112233", alignment=8, margin_v=60))
    assert "Style: Default,Noto Sans Thai,64,&H00332211," in ass
    assert ",8,40,40,60,1" in ass  # alignment + margin_v landed


def test_render_ffmpeg_command_font_override(tmp_path):
    # F8: a per-request font overrides RENDER_FONT in the subtitles filter.
    from app import render

    cmd = render.ffmpeg_command(
        tmp_path / "a.wav", tmp_path / "s.ass", tmp_path / "o.mp4", font="Sarabun"
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.endswith("force_style=FontName=Sarabun")


def test_render_ffmpeg_command_background_image(tmp_path):
    # O1: a background image swaps the lavfi colour for a looped, scaled+cropped
    # still under the burned subtitles.
    from app import render

    img = tmp_path / "bg.jpg"
    cmd = render.ffmpeg_command(
        tmp_path / "a.wav", tmp_path / "s.ass", tmp_path / "o.mp4", background_image=img
    )
    assert "-loop" in cmd and cmd[cmd.index("-loop") + 1] == "1"
    assert str(img) in cmd                          # image is an input
    assert not any(a.startswith("color=") for a in cmd)  # no solid bg
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith(f"scale={render.RENDER_WIDTH}:{render.RENDER_HEIGHT}")
    assert "crop=" in vf and "subtitles=" in vf     # cover-crop then burn subs
    assert "-shortest" in cmd                        # audio length governs


def test_is_valid_background_image_rejects_bad_extension(tmp_path):
    # O1: extension pre-filter rejects non-image suffixes WITHOUT calling ffprobe.
    from app import render

    bad = tmp_path / "evil.txt"
    bad.write_bytes(b"not an image")
    assert render.is_valid_background_image(bad) is False
    assert render.is_valid_background_image(tmp_path / "missing.png") is False  # ffprobe fails -> False


def test_is_valid_background_image_accepts_probed_image(tmp_path, monkeypatch):
    # O1: a .png whose ffprobe reports a real image codec is accepted.
    from app import render

    img = tmp_path / "ok.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # header only; ffprobe is mocked
    monkeypatch.setattr(
        render.subprocess, "run",
        lambda *a, **k: type("R", (), {
            "stdout": '{"streams":[{"codec_name":"png","width":1280,"height":720}]}'})(),
    )
    assert render.is_valid_background_image(img) is True


def test_is_valid_background_image_rejects_decompression_bomb(tmp_path, monkeypatch):
    # O1 security: a tiny file declaring huge dimensions (decompression bomb) is
    # rejected on the declared width/height BEFORE ffmpeg allocates for it.
    from app import render

    img = tmp_path / "bomb.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(
        render.subprocess, "run",
        lambda *a, **k: type("R", (), {
            "stdout": '{"streams":[{"codec_name":"png","width":50000,"height":50000}]}'})(),
    )
    assert render.is_valid_background_image(img) is False
    # exactly at the cap is allowed; one over is not
    monkeypatch.setattr(render, "MAX_BG_IMAGE_DIM", 4096)
    for dims, ok in (((4096, 4096), True), ((4097, 100), False), ((100, 4097), False)):
        monkeypatch.setattr(
            render.subprocess, "run",
            lambda *a, _d=dims, **k: type("R", (), {
                "stdout": f'{{"streams":[{{"codec_name":"png","width":{_d[0]},"height":{_d[1]}}}]}}'})(),
        )
        assert render.is_valid_background_image(img) is ok


def test_render_video_rejects_invalid_background(tmp_path, monkeypatch):
    # O1: render_video refuses an invalid background before spending ffmpeg.
    import pytest

    from app import render

    (tmp_path / "a.m4a").write_bytes(b"x")
    monkeypatch.setattr(render, "is_valid_background_image", lambda p: False)
    with pytest.raises(RuntimeError, match="invalid background image"):
        render.render_video(tmp_path / "a.m4a", "[Events]", tmp_path,
                            background_image=tmp_path / "bg.png")


def test_lrc_and_ass_build():
    line_words = [Word(text="ฉัน", start=1.0, end=1.5), Word(text="คิด", start=1.5, end=2.0)]
    lines = to_lines([*line_words], [line_words])
    lrc = to_lrc(lines)
    assert lrc.startswith("[00:01.00]")
    assert "ฉันคิด" in lrc

    ass = to_ass(lines)
    assert "[Events]" in ass
    assert "\\k50" in ass  # 0.5s -> 50 centiseconds
    assert "Dialogue:" in ass


def test_ass_k_tags_cover_full_line_span_with_gaps():
    """The \\k karaoke clock must equal End-Start, including inter-word silence,
    so the highlight doesn't finish early on gappy luk-thung lines (bug #1)."""
    import re

    # 3 words, each 0.4s sung + 0.35s gap after -> line span 1.85s, sung 1.2s.
    words = [
        Word(text="ก", start=0.0, end=0.4),
        Word(text="ข", start=0.75, end=1.15),
        Word(text="ค", start=1.50, end=1.90),
    ]
    lines = to_lines([*words], [words])
    ass = to_ass(lines)
    dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")][0]
    # sum of all \k centiseconds == (line.end - line.start) * 100 = 190
    total_k = sum(int(n) for n in re.findall(r"\\k(\d+)", dialogue))
    assert total_k == 190, f"\\k total {total_k} != line span 190cs (gaps dropped)"
    # gaps are present as empty {\k35} tags between words
    assert "{\\k35}{\\k40}" in dialogue  # 0.35s gap then 0.4s word


def _seq(texts, start=0.0, dur=0.4, gap=0.0):
    """Build a run of back-to-back Words (override .start to inject gaps)."""
    from app.lrc import Word as _W  # same Word

    words, t = [], start
    for tx in texts:
        words.append(_W(text=tx, start=t, end=t + dur))
        t += dur + gap
    return words


def test_split_words_breaks_on_silence_gap():
    from app import lrc

    words = _seq(["ก", "ข"]) + [Word(text="ค", start=10.0, end=10.4)]  # 8s gap before ค
    chunks = lrc._split_words(words)
    assert [[w.text for w in c] for c in chunks] == [["ก", "ข"], ["ค"]]


def test_split_words_breaks_on_max_duration():
    from app import lrc

    # 25 contiguous words x0.4s = 10s, no gaps -> must break before 7s cap.
    words = _seq(["x"] * 25, dur=0.4)
    chunks = lrc._split_words(words)
    assert len(chunks) > 1
    for c in chunks:
        assert (c[-1].end - c[0].start) <= lrc.LINE_MAX_DUR_SEC + 1e-9


def test_split_words_breaks_on_char_width():
    from app import lrc

    # 40 single-char words, tiny durations -> char cap (30) trips before time.
    words = _seq(["ก"] * 40, dur=0.05)
    chunks = lrc._split_words(words)
    assert len(chunks) >= 2
    for c in chunks:
        assert sum(len(w.text) for w in c) <= lrc.LINE_MAX_CHARS


def test_split_words_is_a_partition_in_order():
    from app import lrc

    words = _seq(["a", "b", "c"]) + [Word(text="d", start=20.0, end=20.4)] + _seq(["e", "f"], start=21.0)
    chunks = lrc._split_words(words)
    flat = [w for c in chunks for w in c]
    assert [w.text for w in flat] == [w.text for w in words]  # every word once, in order
    assert all(c for c in chunks)  # no empty line


def test_to_lines_subbreaks_a_verse_length_segment():
    # A single 50s ASR "verse" segment must become several karaoke lines.
    verse = _seq(["คำ"] * 30, dur=1.5)  # 30 words x1.5s = 45s on one segment
    lines = to_lines([*verse], [verse])
    assert len(lines) > 1
    # And the rendered LRC now carries multiple timestamps, not one blob.
    lrc_text = to_lrc(lines)
    assert lrc_text.count("[") >= 2


# --- GPU VRAM discipline (PRD 5.1, "never co-resident" on the 4 GB GTX 1650) ---


def test_release_separator_moves_weights_off_gpu_and_nulls_refs(monkeypatch):
    # §1 (Critical): separation must free the Demucs weights NOW, not rely on the
    # next stage's gc. _release_separator moves any nn.Module off the GPU, drops
    # the library's strong refs, and forces a cuda cleanup.
    from app import separate

    moved = []

    class FakeModule:  # quacks like an nn.Module (has .to + .parameters)
        def parameters(self):
            return iter([])

        def to(self, dev):
            moved.append(dev)

    class FakeArch:
        def __init__(self):
            self.demucs_model_instance = FakeModule()

    class FakeSeparator:
        def __init__(self):
            self.model_instance = FakeArch()

    cleaned = []
    monkeypatch.setattr(separate, "_cuda_cleanup", lambda: cleaned.append(True))

    sep = FakeSeparator()
    arch = sep.model_instance
    separate._release_separator(sep)

    assert moved == ["cpu"]                       # weights moved off the GPU
    assert arch.demucs_model_instance is None     # inner nn.Module ref dropped
    assert sep.model_instance is None             # library's strong ref nulled
    assert cleaned == [True]                       # gc + empty_cache invoked


def test_release_separator_is_best_effort_and_always_cleans(monkeypatch):
    # Cleanup must never fail a good separation, even with no model present.
    from app import separate

    cleaned = []
    monkeypatch.setattr(separate, "_cuda_cleanup", lambda: cleaned.append(True))

    class Bare:  # no model_instance attribute at all
        pass

    separate._release_separator(Bare())
    separate._release_separator(None)
    assert cleaned == [True, True]  # cuda cleanup still ran both times


def test_align_load_oom_retries_whole_stage_on_cpu(monkeypatch, tmp_path):
    # §2 (High): a load-time CUDA OOM must retry the WHOLE align stage on CPU
    # (real char timing) instead of silently degrading the song to interpolation.
    import types

    from app import align
    from app.asr import Segment

    devices = []

    def fake_load(lang, device):
        devices.append(device)
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return ("model", {"meta": True})

    monkeypatch.setattr(align, "_load_align_model", fake_load)
    monkeypatch.setattr(align, "_align_device", lambda: "cuda")
    monkeypatch.setattr(align, "free_model", lambda: None)
    fake_wx = types.SimpleNamespace(
        load_audio=lambda p: "audio",
        align=lambda *a, **k: {"segments": []},
    )
    monkeypatch.setitem(sys.modules, "whisperx", fake_wx)

    segs = [Segment(text="ก", start=0.0, end=1.0)]
    res = align.align(str(tmp_path / "v.wav"), segs)

    assert devices == ["cuda", "cpu"]   # OOM on GPU -> reloaded on CPU
    assert res.total_segments == 1      # stage ran (did not whole-song degrade)


def test_align_non_oom_load_failure_degrades_without_cpu_retry(monkeypatch, tmp_path):
    # A non-OOM load failure (e.g. missing model) must NOT trigger the CPU retry;
    # it degrades the whole song as before.
    import types

    from app import align
    from app.asr import Segment

    devices = []

    def fake_load(lang, device):
        devices.append(device)
        raise RuntimeError("model not found on hub")

    monkeypatch.setattr(align, "_load_align_model", fake_load)
    monkeypatch.setattr(align, "_align_device", lambda: "cuda")
    monkeypatch.setattr(align, "free_model", lambda: None)
    monkeypatch.setitem(
        sys.modules, "whisperx", types.SimpleNamespace(load_audio=lambda p: "audio")
    )

    segs = [Segment(text="ก", start=0.0, end=1.0)]
    res = align.align(str(tmp_path / "v.wav"), segs)

    assert devices == ["cuda"]          # no CPU retry for a non-OOM failure
    assert res.char_map is None         # degraded whole song
    assert res.degraded_segments == 1
