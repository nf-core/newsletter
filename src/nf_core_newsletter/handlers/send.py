"""Monthly send — triggered by EventBridge Scheduler.

1. Resolve the edition (an explicit `{"year","month"}` event payload forces one;
   otherwise the latest from the website RSS feed).
2. On the scheduled (RSS) path, guard against re-sending an old edition: if the
   resolved edition isn't for the current month, the website likely hasn't
   published this month's newsletter yet, so skip rather than mail last month's
   edition a second time. Pass `{"force": true}` to override.
3. Fetch the rendered `/newsletter/<y>/<m>/email` HTML from nf-co.re.
4. SendEmail it to every contact subscribed to the topic, with
   ListManagementOptions so SES injects the unsubscribe link and applies
   suppression. Per-recipient failures are logged and counted, not fatal.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from nf_core_newsletter import config, content, ses

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _resolve_edition(event: dict[str, Any]) -> tuple[content.Edition, bool]:
    """Resolve the edition to send and whether the recency check should be skipped.

    An explicit ``{"year","month"}`` payload is a deliberate (re-)send, so it
    bypasses the check; the scheduled path can force past it with ``force``.
    """
    year, month = event.get("year"), event.get("month")
    if isinstance(year, int) and isinstance(month, int):
        return content.edition_for(year, month), True
    return content.latest_edition(), bool(event.get("force"))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    edition, skip_recency_check = _resolve_edition(event if isinstance(event, dict) else {})

    if not skip_recency_check and not content.is_current(edition):
        logger.warning(
            "Skipping send: latest edition %04d-%02d is not the current month — "
            "the website likely hasn't published this month's newsletter yet. "
            "Send it explicitly with a {year, month} payload, or {force: true}, to override.",
            edition.year,
            edition.month,
        )
        tag = f"{edition.year:04d}-{edition.month:02d}"
        return {"edition": tag, "sent": 0, "failed": 0, "skipped": "stale-edition"}

    html = content.fetch_email_html(edition)
    logger.info("Sending edition %04d-%02d: %s", edition.year, edition.month, edition.title)

    # Sleep only when ahead of schedule, so per-call API latency isn't
    # double-counted against the rate.
    rate = float(config.require("SEND_RATE_PER_SEC"))
    interval = 1.0 / rate if rate > 0 else 0.0
    next_at = time.monotonic()

    sent = 0
    failed = 0
    for email in ses.iter_subscribed():
        if interval:
            now = time.monotonic()
            if now < next_at:
                time.sleep(next_at - now)
            next_at = max(next_at + interval, time.monotonic())
        try:
            ses.send_email(to=email, subject=edition.title, html_body=html, list_management=True)
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Failed to send to a recipient")

    logger.info("Edition %04d-%02d sent: %d ok, %d failed", edition.year, edition.month, sent, failed)
    return {"edition": f"{edition.year:04d}-{edition.month:02d}", "sent": sent, "failed": failed}
