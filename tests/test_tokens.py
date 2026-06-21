"""Tests for the HMAC confirmation-token signing/verification."""

from __future__ import annotations

from nf_core_newsletter.tokens import make_token, verify_token


def test_roundtrip() -> None:
    token = make_token("user@example.com")
    assert verify_token(token, max_age_seconds=3600) == "user@example.com"


def test_expired_token_rejected() -> None:
    token = make_token("user@example.com", issued_at=0)
    assert verify_token(token, max_age_seconds=3600) is None


def test_tampered_signature_rejected() -> None:
    token = make_token("user@example.com")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert verify_token(tampered, max_age_seconds=3600) is None


def test_garbage_rejected() -> None:
    assert verify_token("not-a-real-token", max_age_seconds=3600) is None
    assert verify_token("", max_age_seconds=3600) is None
