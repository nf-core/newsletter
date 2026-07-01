"""Tests for the monthly send handler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from nf_core_newsletter import content, ses
from nf_core_newsletter.handlers import send


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEND_RATE_PER_SEC", "0")


@pytest.fixture(autouse=True)
def _now_is_june_2026(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin "now" so the recency check treats the June 2026 fixtures as current.
    monkeypatch.setattr(content, "_now", lambda: datetime(2026, 6, 3, tzinfo=UTC))


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


def test_skips_stale_edition_on_scheduled_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Website hasn't published June's edition yet, so the latest is still May's,
    # which was already sent last month. The scheduled send must not re-send it.
    monkeypatch.setattr(content, "latest_edition", lambda: content.Edition(2026, 5, "nf-core Newsletter - May 2026"))
    fetched = False

    def _fetch(_edition: content.Edition) -> str:
        nonlocal fetched
        fetched = True
        return "x"

    monkeypatch.setattr(content, "fetch_email_html", _fetch)
    monkeypatch.setattr(ses, "iter_subscribed", lambda: iter(["a@b.com"]))

    resp = send.handler({}, None)

    assert resp == {"edition": "2026-05", "sent": 0, "failed": 0, "skipped": "stale-edition"}
    assert not fetched  # bailed out before fetching or sending anything


def test_force_overrides_recency_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "latest_edition", lambda: content.Edition(2026, 5, "nf-core Newsletter - May 2026"))
    monkeypatch.setattr(content, "fetch_email_html", lambda _edition: "x")
    monkeypatch.setattr(ses, "iter_subscribed", lambda: iter(["a@b.com"]))
    monkeypatch.setattr(ses, "send_email", lambda **_kwargs: "id")

    resp = send.handler({"force": True}, None)

    assert resp == {"edition": "2026-05", "sent": 1, "failed": 0}


def test_explicit_stale_edition_still_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicit {year, month} payload is a deliberate re-send: recency check bypassed.
    monkeypatch.setattr(content, "fetch_email_html", lambda _edition: "x")
    monkeypatch.setattr(ses, "iter_subscribed", lambda: iter(["a@b.com"]))
    monkeypatch.setattr(ses, "send_email", lambda **_kwargs: "id")

    resp = send.handler({"year": 2025, "month": 1}, None)

    assert resp == {"edition": "2025-01", "sent": 1, "failed": 0}
