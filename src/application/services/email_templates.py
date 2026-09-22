"""Branded transactional email content.

Kept separate from the transport adapters so the copy and markup are shared by
every backend (console, SMTP, Resend) and can be rendered — and asserted on —
without a transport or network.

Email HTML is not web HTML. The constraints that shaped this file:

* **Tables, not flex/grid.** Outlook renders with Word's layout engine, which
  ignores modern layout entirely. A single-column table is the only structure
  that is stable across Outlook, Gmail, Apple Mail and Thunderbird.
* **Inline styles only.** Gmail strips ``<style>`` blocks in many contexts, and
  a stylesheet-based design therefore degrades unpredictably.
* **No external images or fonts.** Remote assets are blocked by default in most
  clients (they render as broken boxes and, worse, a remote image is a tracking
  pixel that can hurt deliverability). The wordmark and accent motif are built
  from styled text and background colours instead, so the message is complete
  and on-brand with images turned off.
* **Plain-text alternative is required.** See ``EmailMessage``.
* **Content is HTML-escaped.** The username in particular is attacker-controlled
  (anyone can register any display name), so interpolating it raw would let a
  registrant inject markup into an email sent to their own address — a
  self-inflicted XSS in the weakest case, but also a spoofing vector when the
  same template is reused.
"""

import html
from urllib.parse import urlsplit

from src.application.dtos.email_dto import EmailMessage

#: Palette from the product design system ("Kinetic Graphite"), pinned here
#: rather than imported from the SPA so an API deploy cannot be broken by a
#: front-end refactor.
_BG = "#F4F5F7"
_CARD = "#FFFFFF"
_HEADER_BG = "#121417"
_HEADER_TEXT = "#ECEEF1"
_TEXT = "#1B1E23"
_MUTED = "#6B7280"
_BORDER = "#E5E7EB"
_PRIMARY = "#5A6BFF"
_PRIMARY_TEXT = "#FFFFFF"
_TINT = "#EEF0FF"

_FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
    "Arial,sans-serif"
)
_MONO_STACK = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"


def build_verification_email(
    *,
    to: str,
    username: str,
    verification_url: str,
    ttl_hours: int,
    app_base_url: str,
    from_name: str = "Transform",
) -> EmailMessage:
    """Render the "activate your account" email.

    ``from_name`` is used for the sign-off only; the envelope sender is the
    transport's concern.
    """
    safe_username = html.escape(username)
    # The URL is assembled by us from configuration, but it is still escaped:
    # a mangled APP_BASE_URL must not be able to inject markup into the button.
    safe_url = html.escape(verification_url, quote=True)
    display_url = html.escape(verification_url)
    brand = html.escape(from_name)
    host = html.escape(urlsplit(app_base_url).netloc or app_base_url)
    hours = f"{ttl_hours} hour" + ("" if ttl_hours == 1 else "s")

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Verify your email address</title>
</head>
<body style="margin:0;padding:0;background-color:{_BG};">
<!-- Preheader: the preview line clients show next to the subject. Hidden in
     the body so the inbox summary says something useful, and padded with
     zero-width spaces so the visible content does not bleed into it. -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">Confirm your address to activate your {brand} account.&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{_BG};">
  <tr>
    <td align="center" style="padding:32px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background-color:{_CARD};border:1px solid {_BORDER};border-radius:14px;overflow:hidden;font-family:{_FONT_STACK};">

        <!-- Brand header -->
        <tr>
          <td style="background-color:{_HEADER_BG};padding:22px 32px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="font-family:{_FONT_STACK};font-size:17px;font-weight:700;color:{_HEADER_TEXT};letter-spacing:-0.01em;">
                  {brand}
                </td>
                <td align="right" style="font-family:{_MONO_STACK};font-size:11px;color:#8A919B;letter-spacing:0.08em;">
                  PDF&#8202;&#8596;&#8202;DOCX
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr><td style="height:3px;background-color:{_PRIMARY};font-size:0;line-height:0;">&nbsp;</td></tr>

        <!-- Body -->
        <tr>
          <td style="padding:36px 32px 8px 32px;">
            <h1 style="margin:0 0 14px 0;font-family:{_FONT_STACK};font-size:23px;line-height:1.3;font-weight:700;color:{_TEXT};">
              Confirm your email address
            </h1>
            <p style="margin:0 0 16px 0;font-family:{_FONT_STACK};font-size:15px;line-height:1.6;color:{_TEXT};">
              Hi {safe_username},
            </p>
            <p style="margin:0 0 24px 0;font-family:{_FONT_STACK};font-size:15px;line-height:1.6;color:{_TEXT};">
              Thanks for signing up. Click the button below to activate your {brand} account and start converting files.
            </p>
          </td>
        </tr>

        <!-- Call to action -->
        <tr>
          <td style="padding:0 32px 8px 32px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td align="center" bgcolor="{_PRIMARY}" style="border-radius:10px;">
                  <a href="{safe_url}"
                     style="display:inline-block;padding:14px 30px;font-family:{_FONT_STACK};font-size:15px;font-weight:600;color:{_PRIMARY_TEXT};text-decoration:none;border-radius:10px;">
                    Activate my account
                  </a>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Expiry + fallback link -->
        <tr>
          <td style="padding:20px 32px 0 32px;">
            <p style="margin:0 0 14px 0;font-family:{_FONT_STACK};font-size:13px;line-height:1.6;color:{_MUTED};">
              This link is valid for {hours}. If the button does not work, copy and paste this address into your browser:
            </p>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{_TINT};border-radius:10px;">
              <tr>
                <td style="padding:14px 16px;">
                  <p style="margin:0;font-family:{_MONO_STACK};font-size:12px;line-height:1.5;color:#3A43B8;word-break:break-all;">
                    <a href="{safe_url}" style="color:#3A43B8;text-decoration:underline;">{display_url}</a>
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Security note -->
        <tr>
          <td style="padding:24px 32px 0 32px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid {_BORDER};">
              <tr>
                <td style="padding-top:20px;">
                  <p style="margin:0;font-family:{_FONT_STACK};font-size:13px;line-height:1.6;color:{_MUTED};">
                    If you did not create a {brand} account, you can safely ignore this email &#8212; no account will be activated. Someone may have typed your address by mistake.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:28px 32px 32px 32px;">
            <p style="margin:0;font-family:{_FONT_STACK};font-size:12px;line-height:1.6;color:{_MUTED};">
              This is an automated message from {brand} and cannot receive replies.<br>
              {host}
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""

    text_body = f"""{from_name}
PDF <-> DOCX

Confirm your email address

Hi {username},

Thanks for signing up. Open the link below to activate your {from_name} account
and start converting files:

{verification_url}

This link is valid for {hours}.

If you did not create a {from_name} account, you can safely ignore this email -
no account will be activated. Someone may have typed your address by mistake.

This is an automated message from {from_name} and cannot receive replies.
{app_base_url}
"""

    return EmailMessage(
        to=to,
        subject=f"Verify your email address to activate your {from_name} account",
        html_body=html_body,
        text_body=text_body,
    )
