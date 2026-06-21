"""Tests for the GET /confirm handler."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nf_core_newsletter import ses
from nf_core_newsletter.handlers import confirm
from nf_core_newsletter.tokens import make_token

if TYPE_CHECKING:
    import pytest


def test_valid_token_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(ses, "get_contact", lambda _email: {"AttributesData": json.dumps({"signup_at": "x"})})
    monkeypatch.setattr(ses, "confirm_contact", lambda email, attrs: captured.update(email=email, attrs=attrs))

    resp = confirm.handler({"queryStringParameters": {"token": make_token("a@b.com")}}, None)

    assert resp["statusCode"] == 200
    assert captured["email"] == "a@b.com"
    assert "confirmed_at" in captured["attrs"]
    assert captured["attrs"]["signup_at"] == "x"  # existing attributes preserved


def test_bad_token_returns_400() -> None:
    resp = confirm.handler({"queryStringParameters": {"token": "garbage"}}, None)
    assert resp["statusCode"] == 400


def test_missing_token_returns_400() -> None:
    resp = confirm.handler({}, None)
    assert resp["statusCode"] == 400


def test_unknown_contact_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ses, "get_contact", lambda _email: None)
    resp = confirm.handler({"queryStringParameters": {"token": make_token("a@b.com")}}, None)
    assert resp["statusCode"] == 400
