"""Auth helpers: HMAC-signed passphrase cookies for browser use, bearer
tokens for headless / CI use.

Bearer tokens are declared in the ``ARENA_API_TOKENS`` env var, comma-separated.
Every value is compared with ``hmac.compare_digest`` to avoid timing leaks.
Bearer-authenticated requests skip the CSRF double-submit check that applies
to cookie-authenticated POSTs: bearer tokens are not carried on cross-site
navigations, so CSRF is not applicable, and requiring both would break
``curl`` / ``requests`` clients.
"""

from __future__ import annotations

import hashlib
import hmac
import os


def make_token(passphrase: str, secret: str) -> str:
    """Create an HMAC token from the passphrase, signed by ``secret``."""
    return hmac.new(secret.encode(), passphrase.encode(), hashlib.sha256).hexdigest()


def load_api_tokens() -> list[str]:
    """Read the ``ARENA_API_TOKENS`` env var and return non-empty tokens."""
    raw = os.environ.get("ARENA_API_TOKENS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def bearer_from_request_headers(auth_header: str | None, x_api_token: str | None) -> str | None:
    """Extract a bearer token from either the ``Authorization`` header or the
    ``X-API-Token`` alias. Returns ``None`` if neither present."""
    if x_api_token:
        return x_api_token.strip()
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def bearer_matches(candidate: str, allowed: list[str]) -> bool:
    """Constant-time check that ``candidate`` matches any allowed token."""
    if not candidate or not allowed:
        return False
    ok = False
    for token in allowed:
        # Do not short-circuit: run compare_digest against every allowed token
        # so the total time doesn't leak the position of the matching token.
        if hmac.compare_digest(candidate, token):
            ok = True
    return ok
