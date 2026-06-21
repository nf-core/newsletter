"""Monthly send — triggered by EventBridge Scheduler.

1. Resolve the edition (an explicit `{"year","month"}` event payload forces one;
   otherwise the latest from the website RSS feed).
2. Fetch the rendered `/newsletter/<y>/<m>/email` HTML from nf-co.re.
3. SendEmail it to every contact subscribed to the topic, with
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


def _resolve_edition(event: dict[str, Any]) -> content.Edition:
    year, month = event.get("year"), event.get("month")
    if isinstance(year, int) and isinstance(month, int):
        return content.edition_for(year, month)
    return content.latest_edition()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    edition = _resolve_edition(event if isinstance(event, dict) else {})
    html = content.add_unsubscribe_footer(content.fetch_email_html(edition))
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
