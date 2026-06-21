"""Tests for edition resolution and RSS parsing."""

from __future__ import annotations

import pytest

from nf_core_newsletter import content

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>nf-core Newsletter - May 2026</title>
    <link>https://nf-co.re/newsletter/2026/05</link>
    <pubDate>Fri, 01 May 2026 00:00:00 GMT</pubDate>
  </item>
  <item>
    <title>nf-core Newsletter - June 2026</title>
    <link>https://nf-co.re/newsletter/2026/06</link>
    <pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


def test_edition_for() -> None:
    edition = content.edition_for(2026, 6)
    assert edition.year == 2026
    assert edition.month == 6
    assert edition.title == "nf-core Newsletter - June 2026"


def test_email_url() -> None:
    assert content.edition_for(2026, 6).email_url == "https://nf-co.re/newsletter/2026/06/email"


def test_latest_edition_picks_newest_pubdate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "_fetch", lambda _url: SAMPLE_RSS)
    edition = content.latest_edition()
    assert (edition.year, edition.month) == (2026, 6)
    assert "June" in edition.title


def test_latest_edition_no_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "_fetch", lambda _url: "<rss><channel></channel></rss>")
    with pytest.raises(RuntimeError):
        content.latest_edition()


def test_add_unsubscribe_footer_before_body_close() -> None:
    html = "<html><body><p>Edition</p></body></html>"
    out = content.add_unsubscribe_footer(html)
    assert "{{amazonSESUnsubscribeUrl}}" in out
    assert out.index("{{amazonSESUnsubscribeUrl}}") < out.index("</body>")
    assert out.count("</body>") == 1


def test_add_unsubscribe_footer_no_body_tag() -> None:
    out = content.add_unsubscribe_footer("<p>no body tag</p>")
    assert out.endswith("</table>")
    assert "{{amazonSESUnsubscribeUrl}}" in out


def test_absolutize_urls() -> None:
    html = (
        '<img src="/_astro/logo.png">'
        '<a href="/newsletter/2026/06">x</a>'
        '<a href="https://nf-co.re/already">keep</a>'
        '<a href="//cdn.example/x">keep</a>'
        '<div style="background:url(/_astro/bg.jpg)"></div>'
    )
    out = content.absolutize_urls(html)
    assert 'src="https://nf-co.re/_astro/logo.png"' in out
    assert 'href="https://nf-co.re/newsletter/2026/06"' in out
    assert "url(https://nf-co.re/_astro/bg.jpg)" in out
    # already-absolute and protocol-relative URLs are left untouched
    assert 'href="https://nf-co.re/already"' in out
    assert 'href="//cdn.example/x"' in out
