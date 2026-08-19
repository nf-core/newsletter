"""POST /subscribe — start the double-opt-in flow.

1. Parse + validate the `email` from the JSON body.
2. Verify the Cloudflare Turnstile token (if enforcement is configured) before
   touching SES — an unverified request must cost nothing and create nothing.
3. If the address is already confirmed, return success without re-sending
   (idempotent — and don't leak which addresses are on the list).
4. Otherwise create/refresh the SES contact as *unconfirmed* (topic OPT_OUT),
   storing consent metadata (timestamp, source IP) in `attributesData`.
5. Mint a signed confirm token and email the confirmation link via SES.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from nf_core_newsletter import config, emails, ses
from nf_core_newsletter.responses import json_response
from nf_core_newsletter.secrets import get_secret
from nf_core_newsletter.tokens import make_token

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Don't re-mail an unconfirmed address more than once per day — an attacker
# spamming the endpoint with a victim's address must not bomb them repeatedly.
_RESEND_COOLDOWN_SECONDS = 24 * 3600

_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TURNSTILE_TIMEOUT_SECONDS = 5


def _parse_body(event: dict[str, Any]) -> dict[str, Any] | None:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _parse_email(data: dict[str, Any]) -> str | None:
    email = data.get("email")
    if not isinstance(email, str):
        return None
    email = email.strip().lower()
    return email if _EMAIL_RE.match(email) else None


def _verify_turnstile(token: Any, remote_ip: str) -> bool:
    """Verify a Cloudflare Turnstile token. Fail CLOSED on any doubt.

    Staged rollout knob: `TURNSTILE_SECRET_PARAM` is the *only* toggle. Unset or
    empty means the website form doesn't carry a widget yet (or an operator has
    deliberately paused enforcement) — verification is skipped and the endpoint
    is unprotected. That's a temporary state, expected only until the form ships
    and the env var is set.

    Once enforcement is on: a missing/invalid token, a Cloudflare `success:
    false`, or Cloudflare being unreachable/erroring/timing out all count as
    failure. Fail-open on a Cloudflare outage would mean the spam relay reopens
    every time Cloudflare has a wobble — a deliberate availability-vs-abuse
    tradeoff in favour of blocking sends when we can't prove the request is human.
    """
    secret_param = config.TURNSTILE_SECRET_PARAM
    if not secret_param:
        return True

    if not isinstance(token, str) or not token:
        return False

    secret = get_secret(secret_param)
    payload = urllib.parse.urlencode({"secret": secret, "response": token, "remoteip": remote_ip}).encode()
    request = urllib.request.Request(_TURNSTILE_VERIFY_URL, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_TURNSTILE_TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read())
    except (OSError, ValueError):
        # Network error, timeout, non-2xx (HTTPError is an OSError subclass), or
        # a malformed JSON body — none of these count as verified. See docstring.
        return False
    return isinstance(result, dict) and result.get("success") is True


def _source_ip(event: dict[str, Any]) -> str:
    http = event.get("requestContext", {}).get("http", {})
    return str(http.get("sourceIp", ""))


def _recently_sent(signup_at: Any) -> bool:
    """True if ``signup_at`` is a UTC timestamp within the resend cooldown.

    A missing or malformed value is treated as "not recent" (fail open to
    sending) rather than crashing the handler.
    """
    if not isinstance(signup_at, str):
        return False
    try:
        sent_at = datetime.fromisoformat(signup_at)
    except ValueError:
        return False
    if sent_at.tzinfo is None:
        return False
    return (datetime.now(UTC) - sent_at).total_seconds() < _RESEND_COOLDOWN_SECONDS


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    data = _parse_body(event)
    email = _parse_email(data) if data is not None else None
    if data is None or email is None:
        return json_response(400, {"error": "invalid_email"})

    if not _verify_turnstile(data.get("cf-turnstile-response"), _source_ip(event)):
        return json_response(400, {"error": "captcha_failed"})

    topic = config.require("TOPIC_NAME")
    existing = ses.get_contact(email)
    if existing is not None:
        if ses.is_subscribed(existing, topic):
            return json_response(200, {"status": "already_subscribed"})
        if _recently_sent(ses.contact_attributes(existing).get(ses.ATTR_SIGNUP_AT)):
            # Same response as a real send — don't leak list membership via timing/shape.
            return json_response(200, {"status": "confirmation_sent"})

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
