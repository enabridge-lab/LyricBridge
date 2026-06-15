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
