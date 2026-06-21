"""Helpers for building API Gateway HTTP API (payload v2) Lambda responses.

CORS headers are added by the HTTP API itself (configured in the CDK stack), so
they are intentionally not set here.
"""

from __future__ import annotations

import json
from typing import Any

from nf_core_newsletter import config

# nf-core brand green (matches the newsletter masthead on the website).
_GREEN = "#22ae63"


def json_response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def html_response(status: int, html: str) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "text/html; charset=utf-8"},
        "body": html,
    }


def landing_page(title: str, message: str) -> str:
    """A self-contained, nf-core-branded HTML page for the /confirm landing view."""
    logo_url = config.require("LOGO_URL")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — nf-core newsletter</title>
<style>
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         background:#f5f5f5; color:#1a1a1a; }}
  .card {{ max-width:560px; margin:10vh auto; background:#fff; border-radius:12px; border-top:4px solid {_GREEN};
          padding:40px; box-shadow:0 1px 4px rgba(0,0,0,.08); text-align:center; }}
  .card img {{ max-width:240px; width:70%; height:auto; margin-bottom:8px; }}
  h1 {{ font-size:1.5rem; color:{_GREEN}; margin:.4em 0 .5em; }}
  p  {{ font-size:1rem; line-height:1.6; color:#444; margin:0 0 1.5em; }}
  a  {{ color:{_GREEN}; font-weight:600; text-decoration:none; }}
</style>
</head>
<body>
  <div class="card">
    <img src="{logo_url}" alt="nf-core/newsletter">
    <h1>{title}</h1>
    <p>{message}</p>
    <p><a href="https://nf-co.re/newsletter">← Back to nf-co.re</a></p>
  </div>
</body>
</html>"""
