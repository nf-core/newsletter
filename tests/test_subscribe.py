"""Tests for the POST /subscribe handler."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nf_core_newsletter import ses
from nf_core_newsletter.handlers import subscribe

if TYPE_CHECKING:
    import pytest


def _event(email: Any, ip: str = "1.2.3.4") -> dict[str, Any]:
    return {
        "body": json.dumps({"email": email}),
        "requestContext": {"http": {"sourceIp": ip}},
    }


def test_invalid_email_returns_400() -> None:
    resp = subscribe.handler(_event("not-an-email"), None)
    assert resp["statusCode"] == 400


def test_malformed_body_returns_400() -> None:
    resp = subscribe.handler({"body": "{not json"}, None)
    assert resp["statusCode"] == 400


def test_new_subscriber_creates_contact_and_sends_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(ses, "get_contact", lambda _email: None)
    monkeypatch.setattr(ses, "upsert_unconfirmed", lambda email, attrs: captured.update(upsert=(email, attrs)))
    monkeypatch.setattr(ses, "send_email", lambda **kwargs: captured.update(email=kwargs) or "msg-1")

    resp = subscribe.handler(_event("User@Example.com"), None)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == "confirmation_sent"
    # email is normalised to lowercase and consent metadata is recorded
    email, attrs = captured["upsert"]
    assert email == "user@example.com"
    assert attrs["signup_ip"] == "1.2.3.4"
    assert "signup_at" in attrs
    # the confirmation email carries a confirm link with a token
    assert "token=" in captured["email"]["html_body"]


def test_already_confirmed_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ses,
        "get_contact",
        lambda _email: {"TopicPreferences": [{"TopicName": "monthly-newsletter", "SubscriptionStatus": "OPT_IN"}]},
    )
    sent: list[Any] = []
    monkeypatch.setattr(ses, "send_email", lambda **kwargs: sent.append(kwargs))

    resp = subscribe.handler(_event("a@b.com"), None)

    assert json.loads(resp["body"])["status"] == "already_subscribed"
    assert sent == []  # no confirmation email re-sent
