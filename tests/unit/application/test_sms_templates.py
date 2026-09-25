"""Unit tests for the phone-verification SMS copy.

The body is asserted against hard constraints, not against a snapshot: one
GSM-7 segment (160 characters), the code present exactly once, no URL, and no
character that would force UCS-2 encoding (which halves the budget to 70).
"""

import pytest

from src.application.services.sms_templates import (
    MAX_SMS_SEGMENT_CHARS,
    build_phone_verification_sms,
    is_gsm7,
)

#: The GSM 03.38 default alphabet, transcribed independently of the module
#: under test so a typo in `_GSM7_CHARS` cannot make these assertions vacuous.
GSM7_ALPHABET = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

TO = "+14155552671"


def test_body_fits_a_single_sms_segment() -> None:
    message = build_phone_verification_sms(to=TO, code="123456", ttl_minutes=10)

    assert len(message.body) <= MAX_SMS_SEGMENT_CHARS


def test_body_uses_only_the_gsm7_alphabet() -> None:
    """One character outside GSM-7 forces UCS-2 and cuts the segment to 70."""
    message = build_phone_verification_sms(to=TO, code="123456", ttl_minutes=10)

    assert is_gsm7(message.body)
    assert all(char in GSM7_ALPHABET for char in message.body), (
        "body contains a character outside the GSM-7 default alphabet"
    )


def test_body_contains_the_code_and_the_recipient() -> None:
    message = build_phone_verification_sms(to=TO, code="042424", ttl_minutes=10)

    assert "042424" in message.body
    assert message.to == TO


def test_body_contains_no_url() -> None:
    """A link would train users to tap URLs in SMS and eat the segment budget."""
    message = build_phone_verification_sms(to=TO, code="123456", ttl_minutes=10)

    lowered = message.body.lower()
    assert "http" not in lowered
    assert "www." not in lowered
    assert "://" not in lowered


def test_body_contains_the_code_exactly_once() -> None:
    message = build_phone_verification_sms(to=TO, code="135791", ttl_minutes=10)

    assert message.body.count("135791") == 1


def test_body_states_the_expiry_in_minutes() -> None:
    message = build_phone_verification_sms(to=TO, code="123456", ttl_minutes=10)

    assert "10 minutes" in message.body


def test_body_uses_the_singular_for_a_one_minute_ttl() -> None:
    message = build_phone_verification_sms(to=TO, code="123456", ttl_minutes=1)

    assert "1 minute." in message.body
    assert "1 minutes" not in message.body


def test_body_is_branded_with_the_app_name() -> None:
    message = build_phone_verification_sms(
        to=TO, code="123456", ttl_minutes=10, app_name="Transform"
    )

    assert message.body.startswith("Transform:")


def test_an_over_long_body_is_rejected_at_build_time() -> None:
    """A silent split into two paid segments is exactly what this guards against."""
    with pytest.raises(ValueError, match="single-segment"):
        build_phone_verification_sms(
            to=TO, code="123456", ttl_minutes=10, app_name="A" * 200
        )


def test_a_non_gsm7_body_is_rejected_at_build_time() -> None:
    with pytest.raises(ValueError, match="GSM-7"):
        build_phone_verification_sms(
            to=TO, code="123456", ttl_minutes=10, app_name="Transform ↔ Files"
        )


def test_longest_supported_ttl_still_fits_one_segment() -> None:
    """The default copy must not be one digit away from splitting."""
    message = build_phone_verification_sms(to=TO, code="999999", ttl_minutes=1440)

    assert len(message.body) <= MAX_SMS_SEGMENT_CHARS
