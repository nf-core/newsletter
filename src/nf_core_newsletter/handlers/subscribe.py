"""POST /subscribe — start the double-opt-in flow.

1. Parse + validate the `email` from the JSON body.
2. If the address is already confirmed, return success without re-sending
   (idempotent — and don't leak which addresses are on the list).
3. Otherwise create/refresh the SES contact as *unconfirmed* (topic OPT_OUT),
   storing consent metadata (timestamp, source IP) in `attributesData`.
4. Mint a signed confirm token and email the confirmation link via SES.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from typing import Any

from nf_core_newsletter import config, emails, ses
from nf_core_newsletter.responses import json_response
from nf_core_newsletter.tokens import make_token

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_email(event: dict[str, Any]) -> str | None:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    email = data.get("email") if isinstance(data, dict) else None
    if not isinstance(email, str):
        return None
    email = email.strip().lower()
    return email if _EMAIL_RE.match(email) else None


def _source_ip(event: dict[str, Any]) -> str:
    http = event.get("requestContext", {}).get("http", {})
    return str(http.get("sourceIp", ""))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    email = _parse_email(event)
    if email is None:
        return json_response(400, {"error": "invalid_email"})

    topic = config.require("TOPIC_NAME")
    existing = ses.get_contact(email)
    if existing is not None and ses.is_subscribed(existing, topic):
        return json_response(200, {"status": "already_subscribed"})

    ses.upsert_unconfirmed(
        email,
        {
            "source": "website",
            ses.ATTR_SIGNUP_IP: _source_ip(event),
            ses.ATTR_SIGNUP_AT: datetime.now(UTC).isoformat(),
        },
    )

    confirm_url = f"{config.require('CONFIRM_URL_BASE')}?token={make_token(email)}"
    ses.send_email(
        to=email,
        subject=emails.CONFIRM_SUBJECT,
        html_body=emails.confirm_email_html(confirm_url),
        text_body=emails.confirm_email_text(confirm_url),
    )
    return json_response(200, {"status": "confirmation_sent"})
