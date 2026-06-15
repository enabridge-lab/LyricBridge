"""Phase A — unit tests for the pure Google-auth + quota helpers (no network).

The networked piece (`verify_google_id_token`) is exercised only via its error
path here; the claim logic it delegates to (`verify_claims`) is covered directly.
"""

from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import auth  # noqa: E402


CLIENT_ID = "123.apps.googleusercontent.com"


def _claims(**over):
    base = {
        "aud": CLIENT_ID,
        "iss": "https://accounts.google.com",
        "sub": "user-abc",
        "exp": 10_000,
    }
    base.update(over)
    return base


def test_verify_claims_accepts_a_good_token_and_returns_sub():
    assert auth.verify_claims(_claims(), CLIENT_ID, now=9_000) == "user-abc"
    # the bare issuer form is also valid
    assert auth.verify_claims(_claims(iss="accounts.google.com"), CLIENT_ID, now=9_000) == "user-abc"


def test_verify_claims_rejects_wrong_audience():
    with pytest.raises(auth.AuthError, match="audience"):
        auth.verify_claims(_claims(aud="someone-else"), CLIENT_ID, now=9_000)


def test_verify_claims_rejects_bad_issuer():
    with pytest.raises(auth.AuthError, match="issuer"):
        auth.verify_claims(_claims(iss="evil.example.com"), CLIENT_ID, now=9_000)


def test_verify_claims_rejects_expired_token_but_allows_small_skew():
    # 60s leeway: 9_950 is within, 10_100 is past.
    assert auth.verify_claims(_claims(exp=10_000), CLIENT_ID, now=9_950) == "user-abc"
    with pytest.raises(auth.AuthError, match="expired"):
        auth.verify_claims(_claims(exp=10_000), CLIENT_ID, now=10_100)


def test_verify_claims_rejects_missing_sub_or_exp_or_clientid():
    with pytest.raises(auth.AuthError, match="subject"):
        auth.verify_claims(_claims(sub=None), CLIENT_ID, now=9_000)
    with pytest.raises(auth.AuthError, match="expiry"):
        auth.verify_claims(_claims(exp=None), CLIENT_ID, now=9_000)
    with pytest.raises(auth.AuthError, match="GOOGLE_CLIENT_ID"):
        auth.verify_claims(_claims(), "", now=9_000)


def test_bearer_token_parses_only_a_proper_bearer_header():
    assert auth.bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"
    assert auth.bearer_token("bearer abc") == "abc"          # case-insensitive scheme
    assert auth.bearer_token("  Bearer   xyz  ") == "xyz"    # tolerant whitespace
    assert auth.bearer_token("Basic abc") is None            # wrong scheme
    assert auth.bearer_token("abc.def") is None              # no scheme
    assert auth.bearer_token("") is None
    assert auth.bearer_token(None) is None
    assert auth.bearer_token("Bearer ") is None              # empty token


def test_monthly_quota_key_is_per_user_per_utc_month():
    jan = datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc)
    feb = datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
    assert auth.monthly_quota_key("u1", jan) == "quota:u1:2026-01"
    assert auth.monthly_quota_key("u1", feb) == "quota:u1:2026-02"  # rolls over
    assert auth.monthly_quota_key("u2", jan) == "quota:u2:2026-01"  # per user


def test_quota_exceeded_caps_at_limit_and_treats_nonpositive_as_unlimited():
    assert auth.quota_exceeded(9, 10) is False
    assert auth.quota_exceeded(10, 10) is True
    assert auth.quota_exceeded(11, 10) is True
    assert auth.quota_exceeded(0, 10) is False
    assert auth.quota_exceeded(None, 10) is False           # absent counter
    assert auth.quota_exceeded(9999, 0) is False            # 0 = unlimited (off switch)
    assert auth.quota_exceeded(9999, -1) is False


def test_verify_google_id_token_wraps_library_failure_as_autherror(monkeypatch):
    # Force google-auth to reject -> AuthError (never leak the raw exception type).
    import types

    fake_id_token = types.SimpleNamespace(
        verify_oauth2_token=lambda *a, **k: (_ for _ in ()).throw(ValueError("bad sig"))
    )
    fake_transport = types.SimpleNamespace(Request=lambda: object())
    monkeypatch.setitem(sys.modules, "google.oauth2.id_token", fake_id_token)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", fake_transport)
    with pytest.raises(auth.AuthError, match="invalid Google token"):
        auth.verify_google_id_token("whatever", CLIENT_ID)
