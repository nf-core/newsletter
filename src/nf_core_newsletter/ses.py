"""Thin wrappers around the SES (API v2) calls this service makes.

SES owns the contact list, suppression, and the hosted unsubscribe page; these
helpers just cover the handful of operations the handlers need.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, cast

import boto3
from botocore.exceptions import ClientError

from nf_core_newsletter import config

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mypy_boto3_sesv2 import SESV2Client

# SES topic subscription statuses.
OPT_IN: Literal["OPT_IN"] = "OPT_IN"
OPT_OUT: Literal["OPT_OUT"] = "OPT_OUT"

# Keys this service stores in a contact's AttributesData JSON blob.
ATTR_SIGNUP_IP = "signup_ip"
ATTR_SIGNUP_AT = "signup_at"
ATTR_CONFIRMED_AT = "confirmed_at"

_client: SESV2Client | None = None


def client() -> SESV2Client:
    global _client
    if _client is None:
        _client = boto3.client("sesv2", region_name=config.require("AWS_REGION"))
    return _client


# ── Contacts ────────────────────────────────────────────────────────────────


def get_contact(email: str) -> dict[str, Any] | None:
    """Return the contact, or ``None`` if it isn't on the list."""
    try:
        resp = client().get_contact(
            ContactListName=config.require("CONTACT_LIST_NAME"),
            EmailAddress=email,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NotFoundException":
            return None
        raise
    return cast("dict[str, Any]", resp)


def contact_attributes(contact: dict[str, Any]) -> dict[str, Any]:
    """Parse a contact's ``AttributesData`` JSON blob (empty dict if absent)."""
    raw = contact.get("AttributesData")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_subscribed(contact: dict[str, Any], topic: str) -> bool:
    """True if the contact is opted in to ``topic`` and not globally unsubscribed."""
    if contact.get("UnsubscribeAll"):
        return False
    for pref in contact.get("TopicPreferences", []):
        if pref.get("TopicName") == topic:
            return bool(pref.get("SubscriptionStatus") == OPT_IN)
    return False


def upsert_unconfirmed(email: str, attributes: dict[str, Any]) -> None:
    """Create the contact opted *out* of the topic, or refresh its attributes.

    A new contact starts OPT_OUT so it never receives an edition before the
    confirm step. An existing contact's topic preference is left untouched.
    """
    list_name = config.require("CONTACT_LIST_NAME")
    topic = config.require("TOPIC_NAME")
    try:
        client().create_contact(
            ContactListName=list_name,
            EmailAddress=email,
            TopicPreferences=[{"TopicName": topic, "SubscriptionStatus": OPT_OUT}],
            AttributesData=json.dumps(attributes),
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "AlreadyExistsException":
            raise
        client().update_contact(
            ContactListName=list_name,
            EmailAddress=email,
            AttributesData=json.dumps(attributes),
        )


def confirm_contact(email: str, attributes: dict[str, Any]) -> None:
    """Opt the contact in to the topic (completes double opt-in).

    Also clears ``UnsubscribeAll`` so that re-subscribing works after a previous
    global unsubscribe (otherwise SES would keep suppressing the topic send even
    with the topic opted in).
    """
    client().update_contact(
        ContactListName=config.require("CONTACT_LIST_NAME"),
        EmailAddress=email,
        TopicPreferences=[{"TopicName": config.require("TOPIC_NAME"), "SubscriptionStatus": OPT_IN}],
        UnsubscribeAll=False,
        AttributesData=json.dumps(attributes),
    )


def iter_subscribed() -> Iterator[str]:
    """Yield the email of every contact opted in to the newsletter topic."""
    list_name = config.require("CONTACT_LIST_NAME")
    topic = config.require("TOPIC_NAME")
    contact_filter = {
        "FilteredStatus": OPT_IN,
        "TopicFilter": {"TopicName": topic, "UseDefaultIfPreferenceUnavailable": False},
    }
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"ContactListName": list_name, "Filter": contact_filter, "PageSize": 1000}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = client().list_contacts(**kwargs)
        for contact in resp.get("Contacts", []):
            yield contact["EmailAddress"]
        next_token = resp.get("NextToken")
        if not next_token:
            return


# ── Sending ───────────────────────────────────────────────────────────────


def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    list_management: bool = False,
) -> str:
    """Send one email via SES, returning the SES message id.

    Set ``list_management=True`` for the newsletter itself so SES injects the
    unsubscribe link and applies topic suppression. Leave it off for the
    transactional confirmation email.
    """
    body: dict[str, Any] = {"Html": {"Data": html_body, "Charset": "UTF-8"}}
    if text_body is not None:
        body["Text"] = {"Data": text_body, "Charset": "UTF-8"}

    kwargs: dict[str, Any] = {
        "FromEmailAddress": config.require("FROM_ADDRESS"),
        "Destination": {"ToAddresses": [to]},
        "Content": {"Simple": {"Subject": {"Data": subject, "Charset": "UTF-8"}, "Body": body}},
        "ConfigurationSetName": config.require("CONFIGURATION_SET_NAME"),
    }
    if list_management:
        kwargs["ListManagementOptions"] = {
            "ContactListName": config.require("CONTACT_LIST_NAME"),
            "TopicName": config.require("TOPIC_NAME"),
        }
    resp = client().send_email(**kwargs)
    return resp["MessageId"]
