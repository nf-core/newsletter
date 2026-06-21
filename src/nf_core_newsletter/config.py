"""Centralised configuration, read from environment variables.

The CDK stack injects these into every Lambda (see infra/stacks/newsletter_stack.py).
Values are resolved lazily so tests can set env vars before first access.

Usage::

    from nf_core_newsletter import config
    config.CONTACT_LIST_NAME
"""

from __future__ import annotations

import os

# Defaults mirror the CDK stack's plain-config values. Anything set to None has
# no default and must come from the environment (or, for the token secret,
# from SSM at runtime — see secrets.py).
_DEFAULTS: dict[str, str | None] = {
    "CONTACT_LIST_NAME": "nf-core-newsletter",
    "TOPIC_NAME": "monthly-newsletter",
    "CONFIGURATION_SET_NAME": "nf-core-newsletter",
    "FROM_ADDRESS": None,
    "WEBSITE_BASE_URL": "https://nf-co.re",
    "RSS_URL": "https://nf-co.re/newsletter/rss.xml",
    "ALLOWED_ORIGIN": "https://nf-co.re",
    # nf-core/newsletter logo shown in the confirm email + landing page.
    "LOGO_URL": "https://nf-co.re/images/logo/nf-core-newsletter-lightbg.png",
    "CONFIRM_TOKEN_SECRET_PARAM": "/nf-core-newsletter/CONFIRM_TOKEN_SECRET",
    # Set on the subscribe Lambda only, once the HTTP API endpoint is known.
    "CONFIRM_URL_BASE": None,
    # Per-recipient send throttle (emails/sec). Matches the account's SES max
    # send rate; bump this if/when AWS raises the account rate.
    "SEND_RATE_PER_SEC": "14",
    # AWS_REGION is set automatically by the Lambda runtime.
    "AWS_REGION": "eu-west-1",
}

_REQUIRED: frozenset[str] = frozenset(["FROM_ADDRESS", "CONFIRM_URL_BASE"])


def _get(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    default = _DEFAULTS.get(name)
    if default is not None:
        return default
    if name in _REQUIRED:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return None


def require(name: str) -> str:
    """Resolve a config value, raising if it is missing.

    Use this wherever a ``str`` (not ``str | None``) is needed.
    """
    value = _get(name)
    if value is None:
        raise RuntimeError(f"Missing required configuration: {name}")
    return value


def __getattr__(name: str) -> str | None:
    if name in _DEFAULTS:
        return _get(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
