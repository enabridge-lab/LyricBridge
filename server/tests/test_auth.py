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


# ── Phase G — approval allowlist helpers ────────────────────────────────────

def test_access_keys_are_namespaced_by_sub():
    assert auth.approved_key("user-abc") == "approved:user-abc"
    assert auth.pending_key("user-abc") == "pending:user-abc"


def test_new_approve_token_is_long_and_unique():
    a, b = auth.new_approve_token(), auth.new_approve_token()
    assert a != b                      # unguessable + random per call
    assert len(a) >= 32                # token_urlsafe(32) ~> 43 chars


def test_token_matches_is_exact_and_safe_on_missing():
    tok = auth.new_approve_token()
    assert auth.token_matches(tok, tok) is True
    assert auth.token_matches(tok, tok + "x") is False
    # a request with no token, or a pending entry with no token, never matches
    assert auth.token_matches("", tok) is False
    assert auth.token_matches(tok, "") is False
    assert auth.token_matches(None, tok) is False
    assert auth.token_matches(tok, None) is False
    assert auth.token_matches(None, None) is False


def test_resolve_pending_matches_sub_then_email_case_insensitively():
    pending = {
        "sub-1": {"email": "Alice@Gmail.com"},
        "sub-2": {"email": "bob@gmail.com"},
    }
    # exact sub wins
    assert auth.resolve_pending("sub-2", pending) == "sub-2"
    # email match, case-insensitive + trimmed
    assert auth.resolve_pending("alice@gmail.com", pending) == "sub-1"
    assert auth.resolve_pending("  BOB@GMAIL.COM ", pending) == "sub-2"
    # no match / empty / junk
    assert auth.resolve_pending("nobody@x.com", pending) is None
    assert auth.resolve_pending("", pending) is None
    assert auth.resolve_pending(None, pending) is None
    assert auth.resolve_pending("alice@gmail.com", {}) is None


def test_resolve_pending_tolerates_entries_without_email():
    pending = {"sub-1": {}, "sub-2": {"email": None}, "sub-3": {"email": "c@x.com"}}
    assert auth.resolve_pending("c@x.com", pending) == "sub-3"
    assert auth.resolve_pending("anything@x.com", pending) is None


def test_notify_cooldown_active_suppresses_repeat_notifies_within_window():
    # no prior request -> always notify
    assert auth.notify_cooldown_active(None, now=1000.0, cooldown_sec=600) is False
    # young pending -> within cooldown -> skip the notify
    assert auth.notify_cooldown_active(1000.0, now=1300.0, cooldown_sec=600) is True
    # past the window -> re-notify (a deliberate nudge)
    assert auth.notify_cooldown_active(1000.0, now=1601.0, cooldown_sec=600) is False
    # boundary: exactly cooldown elapsed -> no longer active
    assert auth.notify_cooldown_active(1000.0, now=1600.0, cooldown_sec=600) is False
    # cooldown disabled -> always notify
    assert auth.notify_cooldown_active(1000.0, now=1001.0, cooldown_sec=0) is False
