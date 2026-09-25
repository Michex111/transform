"""Unit tests for the verification email template.

The assertions focus on the things that make an email *work* rather than on
layout: the link must be present and usable in both parts, user-supplied content
must be escaped, and the message must not depend on any remote asset.
"""

import re

from src.application.services.email_templates import (
    build_password_reset_email,
    build_verification_email,
)

URL = "https://transform-web.onrender.com/verify-email?token=abc123-_XYZ"
RESET_URL = "https://transform-web.onrender.com/reset-password?token=abc123-_XYZ"


def _build(**overrides):
    kwargs = {
        "to": "user@example.com",
        "username": "ada",
        "verification_url": URL,
        "ttl_hours": 24,
        "app_base_url": "https://transform-web.onrender.com",
        "from_name": "Transform",
    }
    kwargs.update(overrides)
    return build_verification_email(**kwargs)


def test_email_addresses_the_recipient_and_names_the_product() -> None:
    message = _build()

    assert message.to == "user@example.com"
    assert "Transform" in message.subject
    assert "verif" in message.subject.lower()


def test_both_bodies_carry_the_verification_link() -> None:
    """The plain-text part is what a text-mode client uses — the link must be there."""
    message = _build()

    assert URL in message.text_body
    # In the HTML it is percent-encoded-safe but otherwise unchanged.
    assert URL in message.html_body


def test_plain_text_body_is_substantive() -> None:
    """HTML-only mail is a spam signal, so the text part must stand alone."""
    message = _build()

    assert len(message.text_body) > 200
    assert "ada" in message.text_body
    assert "24 hours" in message.text_body


def test_expiry_is_rendered_from_the_configured_ttl() -> None:
    assert "48 hours" in _build(ttl_hours=48).text_body
    # Singular for a one-hour TTL — "1 hours" reads as a bug in a security email.
    assert "1 hour." in _build(ttl_hours=1).text_body


def test_username_is_html_escaped() -> None:
    """The username is attacker-controlled (anyone can register any name).

    Interpolating it raw would let a registrant inject markup into a message the
    provider then signs and sends.
    """
    message = _build(username='<img src=x onerror="alert(1)">')

    assert "<img src=x" not in message.html_body
    assert "&lt;img src=x" in message.html_body
    assert "onerror=" not in message.html_body or "&quot;" in message.html_body


def test_no_remote_images_or_fonts_are_referenced() -> None:
    """Remote assets are blocked by default and a remote image is a tracking pixel."""
    html_body = _build().html_body

    assert "<img" not in html_body.lower()
    assert "@import" not in html_body
    # The only http(s) URLs may be the verification link itself.
    urls = re.findall(r"https?://[^\s\"'<>)]+", html_body)
    assert urls, "expected the verification URL to be present"
    assert all(url.startswith("https://transform-web.onrender.com") for url in urls)


def test_no_style_blocks_or_external_stylesheets() -> None:
    """Gmail strips <style> in many contexts, so everything must be inline."""
    html_body = _build().html_body

    assert "<style" not in html_body.lower()
    assert "<link" not in html_body.lower()


def test_tables_are_presentational_for_screen_readers() -> None:
    """Layout tables must be marked so they are not announced as data tables."""
    html_body = _build().html_body

    tables = re.findall(r"<table[^>]*>", html_body)
    assert len(tables) >= 2
    assert all('role="presentation"' in table for table in tables)
    assert all('cellpadding="0"' in table for table in tables)


def test_sender_name_is_used_for_the_sign_off_and_branding() -> None:
    message = _build(from_name="Acme Convert")

    assert "Acme Convert" in message.html_body
    assert "Acme Convert" in message.text_body
    assert "Acme Convert" in message.subject


def test_sender_name_is_escaped_in_html() -> None:
    message = _build(from_name='<script>bad()</script>')

    assert "<script>" not in message.html_body
    assert "&lt;script&gt;" in message.html_body


def test_malformed_base_url_cannot_inject_markup() -> None:
    """APP_BASE_URL is configuration, but it still must not be able to break out."""
    message = _build(app_base_url='https://x.test"><script>a</script>')

    assert "<script>a</script>" not in message.html_body


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


def _build_reset(**overrides):
    kwargs = {
        "to": "user@example.com",
        "username": "ada",
        "reset_url": RESET_URL,
        "ttl_minutes": 60,
        "app_base_url": "https://transform-web.onrender.com",
        "from_name": "Transform",
    }
    kwargs.update(overrides)
    return build_password_reset_email(**kwargs)


def test_reset_subject_names_the_product() -> None:
    message = _build_reset()

    assert message.to == "user@example.com"
    assert message.subject == "Reset your Transform password"
    assert "Acme Convert" in _build_reset(from_name="Acme Convert").subject


def test_both_reset_bodies_carry_the_reset_link() -> None:
    """The plain-text part is what a text-mode client uses — the link must be there."""
    message = _build_reset()

    assert RESET_URL in message.text_body
    assert RESET_URL in message.html_body


def test_reset_plain_text_body_is_substantive() -> None:
    """HTML-only mail is a spam signal, so the text part must stand alone."""
    message = _build_reset()

    assert len(message.text_body) > 200
    assert "ada" in message.text_body


def test_reset_button_uses_the_frozen_label() -> None:
    assert "Choose a new password" in _build_reset().html_body


def test_reset_ttl_is_rendered_with_correct_singular_and_plural() -> None:
    # Whole hours read as hours, so "60 minutes" never ships in the copy.
    assert "This link is valid for 1 hour." in _build_reset(ttl_minutes=60).text_body
    assert "This link is valid for 2 hours." in _build_reset(ttl_minutes=120).text_body
    assert "This link is valid for 15 minutes." in _build_reset(ttl_minutes=15).text_body
    # Singular for a one-minute TTL — "1 minutes" reads as a bug.
    assert "This link is valid for 1 minute." in _build_reset(ttl_minutes=1).text_body


def test_reset_email_carries_the_security_note() -> None:
    message = _build_reset()

    assert "If you did not request a password reset" in message.text_body
    assert "If you did not request a password reset" in message.html_body
    assert "your password will not change" in message.html_body


def test_reset_username_is_html_escaped() -> None:
    """The username is attacker-controlled (anyone can register any name)."""
    message = _build_reset(username='<img src=x onerror="alert(1)">')

    assert "<img src=x" not in message.html_body
    assert "&lt;img src=x" in message.html_body


def test_reset_email_references_no_remote_assets() -> None:
    """Remote assets are blocked by default and a remote image is a tracking pixel."""
    html_body = _build_reset().html_body

    assert "<img" not in html_body.lower()
    assert "@import" not in html_body
    assert "<style" not in html_body.lower()
    assert "<link" not in html_body.lower()


def test_reset_email_contains_only_the_reset_url() -> None:
    html_body = _build_reset().html_body

    urls = re.findall(r"https?://[^\s\"'<>)]+", html_body)
    assert urls, "expected the reset URL to be present"
    assert all(url.startswith("https://transform-web.onrender.com") for url in urls)


def test_reset_tables_are_presentational_for_screen_readers() -> None:
    html_body = _build_reset().html_body

    tables = re.findall(r"<table[^>]*>", html_body)
    assert len(tables) >= 2
    assert all('role="presentation"' in table for table in tables)
    assert all('cellpadding="0"' in table for table in tables)


def test_reset_brand_name_is_escaped() -> None:
    message = _build_reset(from_name='<script>bad()</script>')

    assert "<script>" not in message.html_body
    assert "&lt;script&gt;" in message.html_body


def test_reset_malformed_base_url_cannot_inject_markup() -> None:
    message = _build_reset(app_base_url='https://x.test"><script>a</script>')

    assert "<script>a</script>" not in message.html_body
