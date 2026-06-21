"""GET /confirm?token=... — complete the double-opt-in flow.

1. Verify the HMAC token (valid signature + not expired) and recover the email.
2. Flip the SES contact to OPT_IN on the topic and record `confirmed_at`.
3. Return a friendly HTML landing page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nf_core_newsletter import ses
from nf_core_newsletter.responses import html_response, landing_page
from nf_core_newsletter.tokens import verify_token

# Confirmation links are valid for 7 days.
_MAX_AGE_SECONDS = 7 * 24 * 3600


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    token = (event.get("queryStringParameters") or {}).get("token", "")
    email = verify_token(token, _MAX_AGE_SECONDS) if token else None
    if email is None:
        return html_response(
            400,
            landing_page(
                "Link invalid or expired",
                "This confirmation link is invalid or has expired. Please sign up again.",
            ),
        )

    contact = ses.get_contact(email)
    if contact is None:
        return html_response(
            400,
            landing_page(
                "Subscription not found",
                "We couldn't find a pending subscription for this address. Please sign up again.",
            ),
        )

    attributes = ses.contact_attributes(contact)
    attributes[ses.ATTR_CONFIRMED_AT] = datetime.now(UTC).isoformat()
    ses.confirm_contact(email, attributes)

    return html_response(
        200,
        landing_page(
            "You're subscribed! 🎉",
            "Thanks for confirming — you'll get the nf-core newsletter once a month. "
            "You can unsubscribe any time using the link in every email.",
        ),
    )
