"""Fetch newsletter content from the nf-core website (the upstream source)."""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from nf_core_newsletter import config

_ROOT_RELATIVE_URL_RE = re.compile(r'(?P<attr>(?:src|href)=")/(?!/)')
_CSS_URL_RE = re.compile(r"url\(/(?!/)")
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_LINK_RE = re.compile(r"/newsletter/(\d{4})/(\d{1,2})")
_USER_AGENT = "nf-core-newsletter/1.0 (+https://nf-co.re)"


@dataclass(frozen=True)
class Edition:
    year: int
    month: int
    title: str

    @property
    def email_url(self) -> str:
        base = config.require("WEBSITE_BASE_URL")
        return f"{base}/newsletter/{self.year}/{self.month:02d}/email"


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        raw: bytes = response.read()
        return raw.decode(charset)


def edition_for(year: int, month: int) -> Edition:
    """Build an Edition for an explicit year/month (e.g. a forced re-send)."""
    title = f"nf-core Newsletter - {_MONTHS[month - 1]} {year}"
    return Edition(year=year, month=month, title=title)


def latest_edition() -> Edition:
    """Determine the most recent edition from the website RSS feed."""
    feed = _fetch(config.require("RSS_URL"))
    root = ElementTree.fromstring(feed)

    best: tuple[float, Edition] | None = None
    for item in root.findall(".//item"):
        link = item.findtext("link") or ""
        match = _LINK_RE.search(link)
        if not match:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        title = (item.findtext("title") or f"nf-core Newsletter - {_MONTHS[month - 1]} {year}").strip()
        pub_date = item.findtext("pubDate") or ""
        when = parsedate_to_datetime(pub_date).timestamp() if pub_date else 0.0
        if best is None or when > best[0]:
            best = (when, Edition(year=year, month=month, title=title))

    if best is None:
        raise RuntimeError("No newsletter editions found in the RSS feed")
    return best[1]


def absolutize_urls(html: str) -> str:
    """Rewrite root-relative ``src``/``href``/``url(...)`` to absolute website URLs.

    The website renders images (and stylesheet links) as root-relative paths like
    ``/_astro/foo.png``. Those resolve fine in a browser but not in an email
    client, so prefix them with the website origin.
    """
    base = config.require("WEBSITE_BASE_URL").rstrip("/")
    html = _ROOT_RELATIVE_URL_RE.sub(rf"\g<attr>{base}/", html)
    return _CSS_URL_RE.sub(f"url({base}/", html)


def fetch_email_html(edition: Edition) -> str:
    """Fetch the rendered, email-ready HTML for an edition, with absolute URLs."""
    return absolutize_urls(_fetch(edition.email_url))


# SES only renders an in-body unsubscribe link where this placeholder appears
# (otherwise it just adds List-Unsubscribe headers). It's replaced with the
# hosted unsubscribe URL when the email is sent with ListManagementOptions, so
# the placeholder must survive verbatim in the HTML body.
_UNSUBSCRIBE_FOOTER = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;">'
    '<tr><td style="padding:24px 20px;border-top:1px solid #eeeeee;text-align:center;'
    "font-size:12px;line-height:1.6;color:#888888;"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
    "You're receiving this because you subscribed to the nf-core newsletter at nf-co.re.<br>"
    '<a href="{{amazonSESUnsubscribeUrl}}" style="color:#888888;text-decoration:underline;">Unsubscribe</a>'
    "</td></tr></table>"
)
_BODY_CLOSE_RE = re.compile(r"</body>", re.IGNORECASE)


def add_unsubscribe_footer(html: str) -> str:
    """Insert the SES unsubscribe-placeholder footer just before ``</body>``.

    Added at send time (every edition uses ListManagementOptions), so SES turns
    the placeholder into a working one-click unsubscribe link in the footer.
    """
    if _BODY_CLOSE_RE.search(html):
        return _BODY_CLOSE_RE.sub(lambda m: _UNSUBSCRIBE_FOOTER + m.group(0), html, count=1)
    return html + _UNSUBSCRIBE_FOOTER
