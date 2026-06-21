"""Shared pytest fixtures.

Sets the env vars the handlers expect and stubs the SSM-backed token secret so
nothing in the suite touches AWS.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _env() -> None:
    os.environ.setdefault("FROM_ADDRESS", "nf-core newsletter <newsletter@nf-co.re>")
    os.environ.setdefault("CONFIRM_URL_BASE", "https://example.invalid/confirm")
    os.environ.setdefault("CONFIRM_TOKEN_SECRET_PARAM", "/nf-core-newsletter/CONFIRM_TOKEN_SECRET")


@pytest.fixture(autouse=True)
def _stub_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # tokens.py binds `get_secret` at import, so patch it where it's used.
    monkeypatch.setattr("nf_core_newsletter.tokens.get_secret", lambda _name: "unit-test-secret-key")
