"""Signed, self-contained confirmation tokens for double opt-in.

A token is ``base64url(payload).base64url(hmac_sha256(payload))`` where the
payload is ``"<email>:<issued_at_unix>"``. Verification recomputes the HMAC with
the shared secret (an SSM SecureString) and checks the age — no server-side
token storage needed.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import time
from hashlib import sha256

from nf_core_newsletter import config
from nf_core_newsletter.secrets import get_secret


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: bytes) -> str:
    key = get_secret(config.require("CONFIRM_TOKEN_SECRET_PARAM")).encode()
    return _b64encode(hmac.new(key, payload, sha256).digest())


def make_token(email: str, issued_at: int | None = None) -> str:
    """Create a signed confirmation token for ``email``."""
    when = int(time.time()) if issued_at is None else issued_at
    payload = f"{email}:{when}".encode()
    return f"{_b64encode(payload)}.{_sign(payload)}"


def verify_token(token: str, max_age_seconds: int) -> str | None:
    """Return the email if ``token`` is valid and unexpired, else ``None``."""
    try:
        payload_b64, signature = token.split(".", 1)
        payload = _b64decode(payload_b64)
    except (ValueError, binascii.Error):
        return None

    if not hmac.compare_digest(_sign(payload), signature):
        return None

    try:
        email, issued_str = payload.decode("utf-8").rsplit(":", 1)
        issued_at = int(issued_str)
    except (ValueError, UnicodeDecodeError):
        return None

    if int(time.time()) - issued_at > max_age_seconds:
        return None
    return email
