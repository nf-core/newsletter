"""Tests for the POST /subscribe handler."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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


def _unconfirmed_contact(signup_at: str | None) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if signup_at is not None:
        attrs["signup_at"] = signup_at
    return {
        "TopicPreferences": [{"TopicName": "monthly-newsletter", "SubscriptionStatus": "OPT_OUT"}],
        "AttributesData": json.dumps(attrs),
    }


def test_repeat_post_within_cooldown_does_not_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    recent = datetime.now(UTC).isoformat()
    monkeypatch.setattr(ses, "get_contact", lambda _email: _unconfirmed_contact(recent))
    upserted: list[Any] = []
    sent: list[Any] = []
    monkeypatch.setattr(ses, "upsert_unconfirmed", lambda *a: upserted.append(a))
    monkeypatch.setattr(ses, "send_email", lambda **kwargs: sent.append(kwargs))

    resp = subscribe.handler(_event("a@b.com"), None)

    # Identical response to the fresh-send path — no way to distinguish the two.
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"status": "confirmation_sent"}
    assert sent == []
    assert upserted == []


def test_repeat_post_after_cooldown_resends(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    monkeypatch.setattr(ses, "get_contact", lambda _email: _unconfirmed_contact(stale))
    monkeypatch.setattr(ses, "upsert_unconfirmed", lambda *a: None)
    sent: list[Any] = []
    monkeypatch.setattr(ses, "send_email", lambda **kwargs: sent.append(kwargs) or "msg-1")

    resp = subscribe.handler(_event("a@b.com"), None)

    assert json.loads(resp["body"])["status"] == "confirmation_sent"
    assert len(sent) == 1


def _check_sends_despite_bad_signup_at(monkeypatch: pytest.MonkeyPatch, contact: dict[str, Any]) -> None:
    monkeypatch.setattr(ses, "get_contact", lambda _email: contact)
    monkeypatch.setattr(ses, "upsert_unconfirmed", lambda *a: None)
    sent: list[Any] = []
    monkeypatch.setattr(ses, "send_email", lambda **kwargs: sent.append(kwargs) or "msg-1")

    resp = subscribe.handler(_event("a@b.com"), None)

    assert json.loads(resp["body"])["status"] == "confirmation_sent"
    assert len(sent) == 1


def test_missing_signup_at_does_not_crash_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    _check_sends_despite_bad_signup_at(monkeypatch, _unconfirmed_contact(None))


def test_malformed_signup_at_does_not_crash_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    _check_sends_despite_bad_signup_at(monkeypatch, _unconfirmed_contact("not-a-timestamp"))
