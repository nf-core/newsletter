"""Body templates for the transactional confirmation email."""

from __future__ import annotations

from nf_core_newsletter import config

CONFIRM_SUBJECT = "Confirm your nf-core newsletter subscription"

# nf-core brand green (matches the newsletter masthead on the website).
_GREEN = "#22ae63"


def confirm_email_html(confirm_url: str) -> str:
    logo_url = config.require("LOGO_URL")
    return f"""<!doctype html>
<html lang="en">
<body style="margin:0;padding:24px 12px;background:#f5f5f5;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
    style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;border-top:4px solid {_GREEN};">
    <tr><td style="padding:32px 40px 0;text-align:center;">
      <img src="{logo_url}" alt="nf-core/newsletter" width="260"
        style="max-width:78%;height:auto;display:inline-block;">
    </td></tr>
    <tr><td style="padding:8px 40px 40px;">
      <h1 style="font-size:22px;color:{_GREEN};margin:20px 0 .6em;text-align:center;">Confirm your subscription</h1>
      <p style="font-size:16px;line-height:1.6;color:#444;margin:0 0 1.2em;">
        Thanks for signing up to the nf-core monthly newsletter. Please confirm your email address to start
        receiving it &mdash; one email a month with community news, pipeline releases and events.
      </p>
      <p style="text-align:center;margin:28px 0;">
        <a href="{confirm_url}" style="display:inline-block;background:{_GREEN};color:#ffffff;
          padding:13px 28px;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px;">
          Confirm subscription</a>
      </p>
      <p style="font-size:13px;line-height:1.6;color:#888;margin:0 0 1em;">
        If the button doesn't work, paste this link into your browser:<br>
        <a href="{confirm_url}" style="color:{_GREEN};word-break:break-all;">{confirm_url}</a>
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="font-size:12px;line-height:1.6;color:#aaa;margin:0;text-align:center;">
        You received this because someone entered this address at nf-co.re. If that wasn't you, just ignore
        this email &mdash; you won't be subscribed and we won't email you again.
      </p>
    </td></tr>
  </table>
</body>
</html>"""


def confirm_email_text(confirm_url: str) -> str:
    return (
        "Thanks for signing up to the nf-core monthly newsletter.\n\n"
        "Please confirm your email address by visiting this link:\n"
        f"{confirm_url}\n\n"
        "If you didn't request this, you can safely ignore this email — nothing will be sent."
    )
