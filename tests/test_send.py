"""Tests for the monthly send handler."""

from __future__ import annotations

from typing import Any

import pytest

from nf_core_newsletter import content, ses
from nf_core_newsletter.handlers import send


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEND_RATE_PER_SEC", "0")


def test_sends_to_all_subscribers_with_list_management(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "latest_edition", lambda: content.Edition(2026, 6, "nf-core Newsletter - June 2026"))
    monkeypatch.setattr(content, "fetch_email_html", lambda _edition: "<html>hi</html>")
    monkeypatch.setattr(ses, "iter_subscribed", lambda: iter(["a@b.com", "c@d.com"]))
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(ses, "send_email", lambda **kwargs: calls.append(kwargs) or "id")

    resp = send.handler({}, None)

    assert resp == {"edition": "2026-06", "sent": 2, "failed": 0}
    assert len(calls) == 2
    assert all(call["list_management"] for call in calls)
    assert all(call["subject"] == "nf-core Newsletter - June 2026" for call in calls)


def test_explicit_edition_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "fetch_email_html", lambda _edition: "<html></html>")
    monkeypatch.setattr(ses, "iter_subscribed", lambda: iter([]))

    resp = send.handler({"year": 2026, "month": 3}, None)

    assert resp["edition"] == "2026-03"
    assert resp["sent"] == 0


def test_per_recipient_failure_is_counted_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "latest_edition", lambda: content.Edition(2026, 6, "t"))
    monkeypatch.setattr(content, "fetch_email_html", lambda _edition: "x")
    monkeypatch.setattr(ses, "iter_subscribed", lambda: iter(["a@b.com"]))

    def boom(**_kwargs: Any) -> str:
        raise RuntimeError("send failed")

    monkeypatch.setattr(ses, "send_email", boom)

    resp = send.handler({}, None)

    assert resp["sent"] == 0
    assert resp["failed"] == 1
