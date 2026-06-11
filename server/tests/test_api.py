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
    monkeypatch.setattr(main, "STEM_ENCODE", False)  # fake stems aren't real WAVs

    client = TestClient(main.app)
    resp = client.post("/karaoke", files={"file": ("song.mp3", b"full song", "audio/mpeg")})

    assert resp.status_code == 200
    body = resp.json()
    assert len(calls) == 1  # separation ran exactly ONCE
    assert body["job_id"]
    assert body["instrumental_url"] == f"/instrumental/{body['job_id']}"
    assert body["words"][0]["text"] == "ฉัน"
    assert body["lrc"].startswith("[00:01.00]")

    # the instrumental is fetchable, and RE-fetchable (TTL store, no pop-on-take
    # — the browser issues multiple range GETs when the audio element seeks)
    got = client.get(body["instrumental_url"])
    assert got.status_code == 200
    assert got.headers["content-type"] == "audio/wav"
    assert got.content == b"instrumental stem bytes"
    assert client.get(body["instrumental_url"]).status_code == 200  # re-fetchable


def test_karaoke_rejects_oversized_upload_with_413(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 0)
    client = TestClient(main.app)
    resp = client.post("/karaoke", files={"file": ("song.mp3", b"x", "audio/mpeg")})
    assert resp.status_code == 413


def test_instrumental_unknown_job_is_404():
    client = TestClient(main.app)
    assert client.get("/instrumental/deadbeefdeadbeef").status_code == 404


def test_get_instrumental_is_refetchable_until_ttl(tmp_path, monkeypatch):
    # The store is TTL-only (no pop-on-take): the browser's audio element makes
    # several range GETs for the same job while seeking.
    monkeypatch.setattr(main, "STEM_ENCODE", False)
    src = tmp_path / "instrumental.wav"
    src.write_bytes(b"audio")
    job_id = main._store_instrumental(src)
    first = main._get_instrumental(job_id)
    assert first is not None and first.exists()
    assert main._get_instrumental(job_id) == first  # still there
    import shutil as _sh
    _sh.rmtree(first.parent, ignore_errors=True)


# --- F1: stems are AAC-encoded before serving --------------------------------


def _fake_encode(src_wav, dest, bitrate=None):
    """Stand-in for render.encode_stem: writes a dummy m4a next to the wav."""
    pathlib.Path(dest).write_bytes(b"fake m4a bytes " * 20)
    return pathlib.Path(dest)


def test_instrumental_is_served_as_m4a_with_range_support(tmp_path, monkeypatch):
    monkeypatch.setattr(main.render, "encode_stem", _fake_encode)
    src = tmp_path / "instrumental.wav"
    src.write_bytes(b"raw wav bytes")
    job_id = main._store_instrumental(src)

    client = TestClient(main.app)
    resp = client.get(f"/instrumental/{job_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mp4"
    assert ".m4a" in resp.headers["content-disposition"]

    # range request -> 206 partial content (FileResponse handles Range natively)
    part = client.get(f"/instrumental/{job_id}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100


def test_stem_encode_failure_falls_back_to_wav(tmp_path, monkeypatch, caplog):
    def boom(src_wav, dest, bitrate=None):
        raise RuntimeError("ffmpeg stem encode failed: simulated")

    monkeypatch.setattr(main.render, "encode_stem", boom)
    src = tmp_path / "instrumental.wav"
    src.write_bytes(b"raw wav bytes")
    with caplog.at_level("WARNING"):
        job_id = main._store_instrumental(src)
    assert any("stem encode failed" in r.message for r in caplog.records)

    client = TestClient(main.app)
    resp = client.get(f"/instrumental/{job_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == b"raw wav bytes"


def test_stem_encode_disabled_keeps_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STEM_ENCODE", False)

    def boom(*a, **k):
        raise AssertionError("encode_stem must not run when STEM_ENCODE=0")

    monkeypatch.setattr(main.render, "encode_stem", boom)
    src = tmp_path / "instrumental.wav"
    src.write_bytes(b"raw wav bytes")
    job_id = main._store_instrumental(src)

    client = TestClient(main.app)
    resp = client.get(f"/instrumental/{job_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"


# --- F4: async job queue (submit -> poll) -------------------------------------

import time as _t


def _wait_job(client, job_id, timeout=10.0):
    """Poll GET /jobs/{id} until the worker thread finishes it (or timeout)."""
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        r = client.get(f"/jobs/{job_id}")
        if r.status_code != 200:
            return r
        if r.json()["status"] in ("done", "error"):
            return r
        _t.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _mock_fast_pipeline(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main.separate, "separate", _fake_separation(tmp_path, calls))
    monkeypatch.setattr(main, "_run_pipeline", lambda *a, **k: _fake_pipeline())
    monkeypatch.setattr(main, "_wav_duration", lambda p: 12.3)
    monkeypatch.setattr(main, "STEM_ENCODE", False)
    return calls


def test_jobs_karaoke_submit_returns_202_and_completes(tmp_path, monkeypatch):
    _mock_fast_pipeline(tmp_path, monkeypatch)
    client = TestClient(main.app)

    resp = client.post("/jobs/karaoke", files={"file": ("song.mp3", b"full song", "audio/mpeg")})
    assert resp.status_code == 202
    body = resp.json()
    job_id = body["job_id"]
    assert body["status_url"] == f"/jobs/{job_id}"

    done = _wait_job(client, job_id)
    st = done.json()
    assert st["status"] == "done"
    assert st["error"] is None
    assert st["stage"] == "done" and st["step"] == 4

    # result is the EXACT /karaoke payload shape, sharing the submit's job_id
    result = st["result"]
    blocking = client.post(
        "/karaoke", files={"file": ("song.mp3", b"full song", "audio/mpeg")}
    ).json()
    assert set(result.keys()) == set(blocking.keys())  # field-by-field shape match
    assert result["job_id"] == job_id
    assert result["instrumental_url"] == f"/instrumental/{job_id}"
    assert result["words"] == blocking["words"]
    assert result["lrc"] == blocking["lrc"]

    # ONE id end-to-end: the parked stems answer under the same job_id
    assert client.get(f"/instrumental/{job_id}").status_code == 200
    assert client.get(f"/vocal/{job_id}").status_code == 200


def test_jobs_karaoke_failed_job_reports_error_shape(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Demucs exploded")

    monkeypatch.setattr(main.separate, "separate", boom)
    monkeypatch.setattr(main, "STEM_ENCODE", False)
    client = TestClient(main.app)
    job_id = client.post(
        "/jobs/karaoke", files={"file": ("song.mp3", b"x", "audio/mpeg")}
    ).json()["job_id"]

    st = _wait_job(client, job_id).json()
    assert st["status"] == "error"
    assert st["result"] is None
    assert st["error"]["stage"] == "separate"
    assert "Demucs exploded" in st["error"]["error"]


def test_jobs_karaoke_queue_overflow_returns_429(monkeypatch):
    monkeypatch.setattr(main, "MAX_QUEUED_JOBS", 1)
    # Park submissions forever: drop them instead of handing to the worker.
    monkeypatch.setattr(main._job_queue, "put", lambda item: None)
    client = TestClient(main.app)
    ids = []
    try:
        r1 = client.post("/jobs/karaoke", files={"file": ("a.mp3", b"x", "audio/mpeg")})
        assert r1.status_code == 202
        ids.append(r1.json()["job_id"])

        r2 = client.post("/jobs/karaoke", files={"file": ("b.mp3", b"x", "audio/mpeg")})
        assert r2.status_code == 429
        assert r2.json()["stage"] == "queue"

        # the waiting job reports its queue position
        st = client.get(f"/jobs/{ids[0]}").json()
        assert st["status"] == "queued"
        assert st["queue_position"] == 1
    finally:
        with main._async_jobs_lock:  # don't pollute other tests' queue counts
            for j in ids:
                main._async_jobs.pop(j, None)


def test_jobs_karaoke_record_expires_after_ttl(tmp_path, monkeypatch):
    _mock_fast_pipeline(tmp_path, monkeypatch)
    client = TestClient(main.app)
    job_id = client.post(
        "/jobs/karaoke", files={"file": ("song.mp3", b"full song", "audio/mpeg")}
    ).json()["job_id"]
    assert _wait_job(client, job_id).json()["status"] == "done"

    with main._async_jobs_lock:  # fast-forward past the result TTL
        main._async_jobs[job_id]["expiry"] = _t.time() - 1
    main._sweep_jobs()
    gone = client.get(f"/jobs/{job_id}")
    assert gone.status_code == 404
    assert gone.json()["stage"] == "queue"


def test_jobs_unknown_job_is_404():
    client = TestClient(main.app)
    assert client.get("/jobs/deadbeefdeadbeef").status_code == 404


def test_jobs_karaoke_oversized_upload_is_413(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 0)
    client = TestClient(main.app)
    resp = client.post("/jobs/karaoke", files={"file": ("song.mp3", b"x", "audio/mpeg")})
    assert resp.status_code == 413


# --- F2: re-render from a parked /karaoke job --------------------------------


def _park_instrumental(tmp_path, monkeypatch, name="instrumental.wav"):
    monkeypatch.setattr(main, "STEM_ENCODE", False)
    src = tmp_path / name
    src.write_bytes(b"instrumental bytes")
    return main._store_instrumental(src)


_RENDER_WORDS = [
    {"text": "ฉัน", "start": 1.0, "end": 1.5},
    {"text": "รัก", "start": 1.5, "end": 2.0},
]


def test_render_job_returns_mp4_from_parked_instrumental(tmp_path, monkeypatch):
    job_id = _park_instrumental(tmp_path, monkeypatch)
    video = tmp_path / "karaoke.mp4"
    video.write_bytes(b"fake mp4 bytes")
    seen = {}

    def fake_render(audio_path, ass, work_dir, **kwargs):
        seen["audio"] = pathlib.Path(audio_path)
        seen["ass"] = ass
        return render.RenderResult(video_path=video, width=1280, height=720, font="Noto Sans Thai")

    monkeypatch.setattr(main.render, "render_video", fake_render)

    client = TestClient(main.app)
    resp = client.post(f"/render/{job_id}", json={"lines": [_RENDER_WORDS]})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"fake mp4 bytes"
    # rendered from the PARKED instrumental (no re-upload) with the EDITED words
    assert seen["audio"].name.startswith("instrumental")
    assert "ฉัน" in seen["ass"] and "Dialogue:" in seen["ass"]


def test_render_job_accepts_flat_words_payload(tmp_path, monkeypatch):
    job_id = _park_instrumental(tmp_path, monkeypatch)
    video = tmp_path / "karaoke.mp4"
    video.write_bytes(b"mp4")
    monkeypatch.setattr(
        main.render, "render_video",
        lambda *a, **k: render.RenderResult(video_path=video, width=1280, height=720, font="F"),
    )
    client = TestClient(main.app)
    resp = client.post(f"/render/{job_id}", json={"words": _RENDER_WORDS})
    assert resp.status_code == 200


def test_render_job_applies_style_params(tmp_path, monkeypatch):
    # F8: style fields shape the server-built ASS and the ffmpeg font override.
    job_id = _park_instrumental(tmp_path, monkeypatch)
    video = tmp_path / "karaoke.mp4"
    video.write_bytes(b"mp4")
    seen = {}

    def fake_render(audio_path, ass, work_dir, **kwargs):
        seen["ass"] = ass
        seen["font"] = kwargs.get("font")
        return render.RenderResult(video_path=video, width=1280, height=720, font="F")

    monkeypatch.setattr(main.render, "render_video", fake_render)
    client = TestClient(main.app)
    resp = client.post(
        f"/render/{job_id}",
        json={
            "lines": [_RENDER_WORDS],
            "font": "Noto Sans Thai",
            "font_size": 64,
            "primary_colour": "112233",
            "highlight_colour": "FF0000",
            "alignment": 8,
            "margin_v": 60,
        },
    )
    assert resp.status_code == 200
    assert "Noto Sans Thai,64," in seen["ass"]
    assert "&H00332211" in seen["ass"]   # 112233 -> BGR
    assert "&H000000FF" in seen["ass"]   # FF0000 -> BGR
    assert seen["font"] == "Noto Sans Thai"


def test_render_job_rejects_bad_style_and_unlisted_font(tmp_path, monkeypatch):
    job_id = _park_instrumental(tmp_path, monkeypatch)
    client = TestClient(main.app)

    r = client.post(f"/render/{job_id}", json={"lines": [_RENDER_WORDS], "font": "Comic Sans; rm -rf /"})
    assert r.status_code == 400 and r.json()["stage"] == "render"

    r = client.post(f"/render/{job_id}", json={"lines": [_RENDER_WORDS], "font_size": 999})
    assert r.status_code == 400 and "style" in r.json()["error"]

    r = client.post(f"/render/{job_id}", json={"lines": [_RENDER_WORDS], "primary_colour": "#FFFFFF"})
    assert r.status_code == 400  # '#' prefix is the client's to strip


def test_render_job_font_allowlist_extends_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_FONTS_EXTRA", "Kanit, Prompt")
    assert "Kanit" in main._allowed_render_fonts()
    assert "Prompt" in main._allowed_render_fonts()
    assert "Sarabun" in main._allowed_render_fonts()


def test_render_job_unknown_job_is_404():
    client = TestClient(main.app)
    resp = client.post("/render/deadbeefdeadbeef", json={"lines": [_RENDER_WORDS]})
    assert resp.status_code == 404
    assert resp.json()["stage"] == "render"


def test_render_job_empty_words_is_400(tmp_path, monkeypatch):
    job_id = _park_instrumental(tmp_path, monkeypatch)
    client = TestClient(main.app)
    for body in ({}, {"lines": []}, {"words": []}, {"lines": [[]]}):
        resp = client.post(f"/render/{job_id}", json=body)
        assert resp.status_code == 400
        assert resp.json()["stage"] == "render"


def test_render_job_bad_word_shape_is_400(tmp_path, monkeypatch):
    job_id = _park_instrumental(tmp_path, monkeypatch)
    client = TestClient(main.app)
    resp = client.post(f"/render/{job_id}", json={"lines": [[{"text": "ก"}]]})  # no start/end
    assert resp.status_code == 400
    assert resp.json()["stage"] == "render"


def test_get_instrumental_renews_the_ttl(tmp_path, monkeypatch):
    # F2: every access must push the expiry out so a long edit session doesn't
    # 404 when the user finally hits render / vocal guide.
    job_id = _park_instrumental(tmp_path, monkeypatch)
    import time as _time
    with main._jobs_lock:
        path, _ = main._jobs[job_id]
        main._jobs[job_id] = (path, _time.time() + 5)  # about to expire

    client = TestClient(main.app)
    assert client.get(f"/instrumental/{job_id}").status_code == 200
    with main._jobs_lock:
        _, expiry = main._jobs[job_id]
    assert expiry > _time.time() + main.INSTRUMENTAL_TTL_SEC - 30  # renewed


def test_touch_job_renews_both_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STEM_ENCODE", False)
    inst = tmp_path / "instrumental.wav"
    inst.write_bytes(b"i")
    voc = tmp_path / "vocals.wav"
    voc.write_bytes(b"v")
    job_id = main._store_instrumental(inst)
    main._store_vocal(job_id, voc)
    import time as _time
    near = _time.time() + 5
    with main._jobs_lock:
        main._jobs[job_id] = (main._jobs[job_id][0], near)
    with main._vocal_jobs_lock:
        main._vocal_jobs[job_id] = (main._vocal_jobs[job_id][0], near)

    main._touch_job(job_id)

    with main._jobs_lock:
        assert main._jobs[job_id][1] > near + 60
    with main._vocal_jobs_lock:
        assert main._vocal_jobs[job_id][1] > near + 60


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


def test_transcribe_words_carry_segment_confidence(monkeypatch):
    # F3: every word inherits its ASR segment's confidence (segment-level on
    # purpose — whisper tokens don't map 1:1 onto PyThaiNLP tokens).
    from app import align, asr

    segs = [
        asr.Segment(text="ฉันรักเธอ", start=0.0, end=2.0,
                    avg_logprob=-0.2, no_speech_prob=0.05),
        asr.Segment(text="คิดถึงเธอ", start=2.0, end=4.0),  # no scores -> None
    ]
    monkeypatch.setattr(main.asr, "transcribe", lambda *a, **k: segs)
    monkeypatch.setattr(main.asr, "free_model", lambda: None)
    monkeypatch.setattr(main.align, "align", lambda *a, **k: align.AlignResult(None, 0, 2))
    monkeypatch.setattr(main.align, "free_model", lambda: None)
    monkeypatch.setattr(main, "_wav_duration", lambda p: 4.0)

    client = TestClient(main.app)
    r = client.post(
        "/transcribe",
        files={"file": ("v.wav", b"x" * 1000, "audio/wav")},
        data={"format": "json"},
    )
    assert r.status_code == 200
    words = r.json()["words"]
    seg1 = [w for w in words if w["start"] < 2.0]
    seg2 = [w for w in words if w["start"] >= 2.0]
    assert seg1 and seg2
    expected = main.asr.segment_confidence(-0.2, 0.05)
    for w in seg1:  # whole segment shares one score
        assert abs(w["confidence"] - expected) < 0.005
    for w in seg2:  # segment without scores -> confidence stays None
        assert w["confidence"] is None


def test_transcribe_words_carry_interpolated_and_roman(monkeypatch):
    # F6 + F7: degraded segments (no char map) -> every word flagged
    # interpolated, and each word gets a romanized reading.
    from app import align, asr

    seg = asr.Segment(text="ฉันรักเธอ", start=0.0, end=2.0)
    monkeypatch.setattr(main.asr, "transcribe", lambda *a, **k: [seg])
    monkeypatch.setattr(main.asr, "free_model", lambda: None)
    monkeypatch.setattr(main.align, "align", lambda *a, **k: align.AlignResult(None, 1, 1))
    monkeypatch.setattr(main.align, "free_model", lambda: None)
    monkeypatch.setattr(main, "_wav_duration", lambda p: 2.0)

    client = TestClient(main.app)
    r = client.post(
        "/transcribe",
        files={"file": ("v.wav", b"x" * 1000, "audio/wav")},
        data={"format": "json"},
    )
    assert r.status_code == 200
    words = r.json()["words"]
    assert words and all(w["interpolated"] is True for w in words)  # F6
    assert all(w["roman"] for w in words)  # F7 (royin is rule-based, never empty here)


def test_transcribe_romanize_disabled_leaves_roman_none(monkeypatch):
    from app import align, asr

    seg = asr.Segment(text="ฉันรักเธอ", start=0.0, end=2.0)
    monkeypatch.setattr(main.asr, "transcribe", lambda *a, **k: [seg])
    monkeypatch.setattr(main.asr, "free_model", lambda: None)
    monkeypatch.setattr(main.align, "align", lambda *a, **k: align.AlignResult(None, 0, 1))
    monkeypatch.setattr(main.align, "free_model", lambda: None)
    monkeypatch.setattr(main, "_wav_duration", lambda p: 2.0)
    monkeypatch.setattr(main, "ROMANIZE", False)

    client = TestClient(main.app)
    r = client.post(
        "/transcribe",
        files={"file": ("v.wav", b"x" * 1000, "audio/wav")},
        data={"format": "json"},
    )
    assert r.status_code == 200
    assert all(w["roman"] is None for w in r.json()["words"])


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

