"""Smoke tests for config resolution and handler imports."""

from __future__ import annotations

from nf_core_newsletter import config


def test_defaults_resolve() -> None:
    assert config.CONTACT_LIST_NAME == "nf-core-newsletter"
    assert config.TOPIC_NAME == "monthly-newsletter"
    assert config.WEBSITE_BASE_URL == "https://nf-co.re"


def test_required_value_present_via_fixture() -> None:
    assert config.FROM_ADDRESS is not None
