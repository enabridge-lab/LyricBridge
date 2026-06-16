"""Phase A — Google Sign-In (stateless): ID-token verification + monthly quota.

Design (docs/ROADMAP_LANDING_AND_OAUTH.md Phase A):
  - The browser signs in with Google Identity Services and sends the resulting
    ID token (a JWT) as `Authorization: Bearer <jwt>` on POST /jobs/karaoke.
  - The Modal `web` app verifies it (signature against Google's JWKS, cached by
    google-auth; aud == GOOGLE_CLIENT_ID; iss; exp), derives the stable user id
    (`sub`), and meters a monthly quota in a modal.Dict keyed by sub + month.
  - NO database, NO session store. Quota lives in a Dict (not durable across a
    redeploy — owner-accepted for the demo).

This module holds the PURE, network-free pieces so they unit-test under
`pytest server/` (the Modal app can't be imported there — no `modal` dep). The
one networked piece, `verify_google_id_token`, is a thin wrapper over google-auth
and is monkeypatched in tests.
"""

from __future__ import annotations

import datetime
import hmac
import secrets

# Google's documented issuers for ID tokens (both forms appear in the wild).
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class AuthError(Exception):
    """Token rejected. `.reason` is a short, safe-to-surface string (no token data)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def verify_claims(claims: dict, client_id: str, now: float | None = None,
                  leeway: int = 60) -> str:
    """Validate decoded ID-token claims and return the user's `sub`.

    PURE (no network): checks audience, issuer, expiry. Raises AuthError on any
    failure. `now` (epoch seconds) is injectable for tests; `leeway` absorbs
    minor clock skew on `exp`. Signature verification happens upstream in
    `verify_google_id_token` — this guards the *claims* a verified token carries.
    """
    if not isinstance(claims, dict):
        raise AuthError("malformed token claims")
    if not client_id:
        # No configured audience to check against → refuse rather than accept any.
        raise AuthError("server has no GOOGLE_CLIENT_ID configured")
    if claims.get("aud") != client_id:
        raise AuthError("token audience mismatch")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("unexpected token issuer")
    sub = claims.get("sub")
    if not sub:
        raise AuthError("token has no subject")
    exp = claims.get("exp")
    try:
        exp = float(exp)
    except (TypeError, ValueError):
        raise AuthError("token has no expiry")
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    if exp + leeway < now:
        raise AuthError("token expired")
    return str(sub)


def bearer_token(authorization_header: str | None) -> str | None:
    """Extract the raw JWT from an `Authorization: Bearer <jwt>` header.

    PURE. Returns None when the header is missing or not a Bearer scheme.
    Case-insensitive on the scheme; tolerant of extra surrounding whitespace.
    """
    if not authorization_header:
        return None
    parts = authorization_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def monthly_quota_key(sub: str, when: datetime.datetime | None = None) -> str:
    """Dict key for a user's monthly quota: `quota:{sub}:{YYYY-MM}` (UTC month).

    PURE. The month component rolls the counter over automatically (a new month
    is simply a new, absent key starting at 0), so no sweep is required for
    correctness — only for housekeeping.
    """
    when = when or datetime.datetime.now(datetime.timezone.utc)
    return f"quota:{sub}:{when.strftime('%Y-%m')}"


def quota_exceeded(used: int, limit: int) -> bool:
    """True when this user has hit their monthly cap. PURE.

    A limit <= 0 means "unlimited" (a deliberate off switch), never "block all".
    """
    if limit <= 0:
        return False
    return (used or 0) >= limit


def verify_google_id_token(token: str, client_id: str) -> dict:
    """Verify a Google ID token's SIGNATURE + standard claims; return its claims.

    Thin wrapper over google-auth, which fetches and CACHES Google's JWKS certs.
    Network-touching, so tests monkeypatch this (the pure `verify_claims` covers
    the claim logic). Raises AuthError on any verification failure.
    """
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except Exception as exc:  # noqa: BLE001 - missing dep is a server config error
        raise AuthError(f"auth library unavailable: {exc}") from exc
    try:
        # google-auth checks signature, exp, and aud (when audience is given), and
        # raises ValueError on anything wrong. It caches the certs across calls.
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=client_id
        )
    except Exception as exc:  # noqa: BLE001 - google raises ValueError/others
        raise AuthError("invalid Google token") from exc
    # Defence in depth: re-check issuer/aud/sub via the pure validator (google-auth
    # already checked aud+exp, but this keeps one consistent rejection path).
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("unexpected token issuer")
    if claims.get("aud") != client_id:
        raise AuthError("token audience mismatch")
    if not claims.get("sub"):
        raise AuthError("token has no subject")
    return claims


# ── Phase G — approval allowlist (pure, network-free; modal_app holds the Dict) ──
#
# The Modal `web` app stores approval state in a durable modal.Dict keyed by:
#   approved:{sub} -> {email, approved_at}
#   pending:{sub}  -> {email, name, requested_at, approve_token}
# These helpers hold the key formatting + the SECURITY-CRITICAL token compare and
# identifier resolution, so `pytest server/` covers them without importing modal.


def approved_key(sub: str) -> str:
    """Dict key for an approved user. PURE."""
    return f"approved:{sub}"


def pending_key(sub: str) -> str:
    """Dict key for a pending access request. PURE."""
    return f"pending:{sub}"


def new_approve_token() -> str:
    """A fresh, unguessable single-use approval token (256 bits, URL-safe)."""
    return secrets.token_urlsafe(32)


def token_matches(provided: str | None, stored: str | None) -> bool:
    """Constant-time compare of an approval token against the stored one. PURE.

    Returns False (never raises) when either side is missing/empty, so a request
    with no token — or a pending entry written before tokens existed — can never
    match. Uses hmac.compare_digest to avoid leaking length/position via timing.
    """
    if not provided or not stored:
        return False
    return hmac.compare_digest(str(provided), str(stored))


def resolve_pending(identifier: str | None, pending: dict) -> str | None:
    """Resolve an owner-supplied identifier (sub OR email) to a pending `sub`. PURE.

    `pending` maps sub -> entry ({email, ...}). An exact sub match wins; otherwise
    we match on email case-insensitively. Returns the sub, or None if nothing
    matches. Owner-facing convenience: emails are easier to paste than subs, but
    we approve by sub internally (email can change).
    """
    if not identifier:
        return None
    if identifier in pending:
        return identifier
    ident = identifier.strip().lower()
    for sub, entry in pending.items():
        email = (entry or {}).get("email")
        if email and str(email).strip().lower() == ident:
            return sub
    return None


def notify_cooldown_active(prev_requested_at, now: float, cooldown_sec: int) -> bool:
    """True when an existing pending request is young enough to SKIP re-notifying
    the owner (anti-spam). PURE.

    No prior request (`prev_requested_at` is None) → False (send the notify).
    `cooldown_sec <= 0` disables the cooldown (always notify). Otherwise the
    notify is suppressed while `now - prev_requested_at < cooldown_sec`.
    """
    if prev_requested_at is None or cooldown_sec <= 0:
        return False
    return (now - float(prev_requested_at)) < cooldown_sec
