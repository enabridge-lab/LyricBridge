"""Fast API contract tests that avoid model downloads."""

from __future__ import annotations

import pathlib
import sys
import zipfile
from io import BytesIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import main, render, separate  # noqa: E402


def test_separate_endpoint_returns_vocals_and_instrumental_zip(tmp_path, monkeypatch):
    vocals = tmp_path / "vocals.wav"
    instrumental = tmp_path / "instrumental.wav"
    vocals.write_bytes(b"vocals wav")
    instrumental.write_bytes(b"instrumental wav")

    def fake_separate(input_path, work_dir):
        return separate.SeparationResult(
            vocals_path=vocals,
            instrumental_path=instrumental,
            model="htdemucs",
            device="cpu",
        )

    monkeypatch.setattr(main.separate, "separate", fake_separate)

    client = TestClient(main.app)
    response = client.post(
        "/separate",
        files={"file": ("song.mp3", b"fake song", "audio/mpeg")},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        assert sorted(zf.namelist()) == ["instrumental.wav", "vocals.wav"]
        assert zf.read("vocals.wav") == b"vocals wav"
        assert zf.read("instrumental.wav") == b"instrumental wav"


def test_render_endpoint_returns_mp4(tmp_path, monkeypatch):
    video = tmp_path / "karaoke.mp4"
    video.write_bytes(b"fake mp4 bytes")

    def fake_render(audio_path, ass, work_dir, **kwargs):
        return render.RenderResult(video_path=video, width=1280, height=720, font="Noto Sans Thai")

    monkeypatch.setattr(main.render, "render_video", fake_render)

    client = TestClient(main.app)
    response = client.post(
        "/render",
        files={"file": ("instrumental.wav", b"fake audio", "audio/wav")},
        data={"ass": "[Events]\nDialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{\\k50}hi"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"fake mp4 bytes"


def test_render_endpoint_rejects_empty_ass():
    client = TestClient(main.app)
    response = client.post(
        "/render",
        files={"file": ("a.wav", b"x", "audio/wav")},
        data={"ass": "   "},
    )
    assert response.status_code == 400


def test_cors_allows_the_static_frontend_origin():
    # The browser player is a separate origin; CORS must let it call the API.
    client = TestClient(main.app)
    response = client.get("/healthz", headers={"Origin": "http://localhost:8080"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in ("*", "http://localhost:8080")


def test_oversized_upload_is_rejected_with_413(monkeypatch):
    # Bug #4: a public deploy could fill the disk; cap uploads.
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 0)  # any non-empty body exceeds 0
    client = TestClient(main.app)
    response = client.post(
        "/separate",
        files={"file": ("song.mp3", b"some audio bytes", "audio/mpeg")},
    )
    assert response.status_code == 413
    assert "MB limit" in response.json()["error"]


def test_version_does_not_load_the_heavy_align_model(monkeypatch):
    # Bug #8: /version must not trigger the ~2.9 GiB align model load.
    def boom(*a, **k):
        raise AssertionError("align_available() must NOT be called by /version")

    monkeypatch.setattr(main.align, "align_available", boom)
    client = TestClient(main.app)
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["align_available"] is True  # configured, not loaded


# --- /karaoke one-upload flow -------------------------------------------------

from app.schemas import TranscribeResponse, Word  # noqa: E402


def _fake_separation(tmp_path, call_counter):
    """Build a separate.separate stand-in that writes real stem files."""

    def fake_separate(input_path, work_dir):
        call_counter.append(1)
        vocals = pathlib.Path(work_dir) / "vocals.wav"
        instrumental = pathlib.Path(work_dir) / "instrumental.wav"
        vocals.write_bytes(b"vocal stem bytes")
        instrumental.write_bytes(b"instrumental stem bytes")
        return separate.SeparationResult(
            vocals_path=vocals,
            instrumental_path=instrumental,
            model="htdemucs_ft.yaml",
            device="cpu",
        )

    return fake_separate


def _fake_pipeline():
    return TranscribeResponse(
        language="th",
        duration_sec=12.3,
        words=[Word(text="ฉัน", start=1.0, end=1.5)],
        lrc="[00:01.00]ฉัน",
        ass="Dialogue: ...",
        aligned=True,
    )


def test_karaoke_separates_once_and_returns_lyrics_plus_job(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main.separate, "separate", _fake_separation(tmp_path, calls))
    monkeypatch.setattr(main, "_run_pipeline", lambda *a, **k: _fake_pipeline())
    monkeypatch.setattr(main, "_wav_duration", lambda p: 12.3)

    client = TestClient(main.app)
    resp = client.post("/karaoke", files={"file": ("song.mp3", b"full song", "audio/mpeg")})

    assert resp.status_code == 200
    body = resp.json()
    assert len(calls) == 1  # separation ran exactly ONCE
    assert body["job_id"]
    assert body["instrumental_url"] == f"/instrumental/{body['job_id']}"
    assert body["words"][0]["text"] == "ฉัน"
    assert body["lrc"].startswith("[00:01.00]")

    # the instrumental is fetchable, then gone after the single stream
    got = client.get(body["instrumental_url"])
    assert got.status_code == 200
    assert got.headers["content-type"] == "audio/wav"
    assert got.content == b"instrumental stem bytes"
    assert client.get(body["instrumental_url"]).status_code == 404  # consumed


def test_karaoke_rejects_oversized_upload_with_413(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 0)
    client = TestClient(main.app)
    resp = client.post("/karaoke", files={"file": ("song.mp3", b"x", "audio/mpeg")})
    assert resp.status_code == 413


def test_instrumental_unknown_job_is_404():
    client = TestClient(main.app)
    assert client.get("/instrumental/deadbeefdeadbeef").status_code == 404


def test_take_instrumental_pops_on_take(tmp_path):
    # #2: claiming a job removes its entry so a racing second take gets None
    # (guarantees a single download even if two GETs race).
    src = tmp_path / "instrumental.wav"
    src.write_bytes(b"audio")
    job_id = main._store_instrumental(src)
    first = main._take_instrumental(job_id)
    assert first is not None and first.exists()
    assert main._take_instrumental(job_id) is None  # already claimed
    # cleanup the parked dir the test created
    import shutil as _sh
    _sh.rmtree(first.parent, ignore_errors=True)


def test_transcribe_reports_degraded_segment_count(monkeypatch, tmp_path):
    # §0 diagnose: /transcribe must expose how many segments degraded so the
    # client can tell aligned timing from interpolated guessing.
    from app import align, asr

    seg = asr.Segment(text="ฉันรักเธอ", start=0.0, end=2.0)
    monkeypatch.setattr(main.asr, "transcribe", lambda *a, **k: [seg])
    monkeypatch.setattr(main.asr, "free_model", lambda: None)
    # 1 of 1 segment degraded (no char map) -> aligned False, count 1
    monkeypatch.setattr(
        main.align, "align", lambda *a, **k: align.AlignResult(None, 1, 1)
    )
    monkeypatch.setattr(main.align, "free_model", lambda: None)
    monkeypatch.setattr(main, "_wav_duration", lambda p: 2.0)

    client = TestClient(main.app)
    r = client.post(
        "/transcribe",
        files={"file": ("v.wav", b"x" * 1000, "audio/wav")},
        data={"format": "json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["aligned"] is False
    assert body["degraded_segment_count"] == 1
    assert body["total_segment_count"] == 1
    assert len(body["words"]) >= 1  # still interpolated, not empty


def test_transcribe_exposes_stage_timings(monkeypatch):
    # §0: per-stage timings appear in the response when EXPOSE_TIMINGS is on.
    from app import align, asr

    seg = asr.Segment(text="ฉันรักเธอ", start=0.0, end=2.0)
    monkeypatch.setattr(main.asr, "transcribe", lambda *a, **k: [seg])
    monkeypatch.setattr(main.asr, "free_model", lambda: None)
    monkeypatch.setattr(main.align, "align", lambda *a, **k: align.AlignResult(None, 0, 1))
    monkeypatch.setattr(main.align, "free_model", lambda: None)
    monkeypatch.setattr(main, "_wav_duration", lambda p: 2.0)
    monkeypatch.setattr(main, "EXPOSE_TIMINGS", True)

    client = TestClient(main.app)
    r = client.post(
        "/transcribe",
        files={"file": ("v.wav", b"x" * 1000, "audio/wav")},
        data={"format": "json"},
    )
    assert r.status_code == 200
    t = r.json()["timings_sec"]
    assert t is not None and "asr" in t and "align" in t and "build" in t


def test_version_exposes_align_load_error_field():
    client = TestClient(main.app)
    body = client.get("/version").json()
    assert "align_load_error" in body  # None unless a load actually failed


def test_karaoke_maps_pipeline_error_to_http(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main.separate, "separate", _fake_separation(tmp_path, calls))
    monkeypatch.setattr(main, "_wav_duration", lambda p: 1.0)

    def boom(*a, **k):
        raise main.PipelineError(422, "no speech detected in vocal stem", "asr")

    monkeypatch.setattr(main, "_run_pipeline", boom)
    client = TestClient(main.app)
    resp = client.post("/karaoke", files={"file": ("song.mp3", b"full song", "audio/mpeg")})
    assert resp.status_code == 422
    assert resp.json()["stage"] == "asr"


def test_karaoke_response_has_vocal_url(tmp_path, monkeypatch):
    """POST /karaoke should return vocal_url pointing to /vocal/<job_id>."""
    calls = []
    monkeypatch.setattr(main.separate, "separate", _fake_separation(tmp_path, calls))
    monkeypatch.setattr(main, "_run_pipeline", lambda *a, **k: _fake_pipeline())
    monkeypatch.setattr(main, "_wav_duration", lambda p: 12.3)

    client = TestClient(main.app)
    resp = client.post("/karaoke", files={"file": ("song.mp3", b"full song", "audio/mpeg")})
    assert resp.status_code == 200
    payload = resp.json()
    assert "vocal_url" in payload
    assert payload["vocal_url"].startswith("/vocal/")


def test_get_vocal_returns_audio(tmp_path, monkeypatch):
    """GET /vocal/<job_id> should return 200 audio/wav."""
    calls = []
    monkeypatch.setattr(main.separate, "separate", _fake_separation(tmp_path, calls))
    monkeypatch.setattr(main, "_run_pipeline", lambda *a, **k: _fake_pipeline())
    monkeypatch.setattr(main, "_wav_duration", lambda p: 12.3)

    client = TestClient(main.app)
    resp = client.post("/karaoke", files={"file": ("song.mp3", b"full song", "audio/mpeg")})
    job_id = resp.json()["job_id"]
    vocal_resp = client.get(f"/vocal/{job_id}")
    assert vocal_resp.status_code == 200
    assert "audio" in vocal_resp.headers["content-type"]


def test_get_vocal_invalid_returns_404():
    """GET /vocal/<invalid> should return 404."""
    client = TestClient(main.app)
    resp = client.get("/vocal/nonexistent_job_id")
    assert resp.status_code == 404

