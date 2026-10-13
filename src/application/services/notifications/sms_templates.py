"""SMS content for the phone-verification flow.

Kept separate from the transport adapters so the copy is shared by every
backend (console, Twilio) and can be rendered — and asserted on — without a
transport or network.

SMS is not email. The constraints that shaped this file:

* **One segment.** GSM-7 encoding fits 160 characters in a single segment.
  Exceeding it means the message is split, billed per part, and can arrive out
  of order or with the second part delayed — which for a verification code
  means the user sees a truncated message. The builder asserts the budget so a
  future copy change cannot silently cross it.
* **GSM-7 only.** The extended/Unicode tables force UCS-2 encoding, which
  drops the segment to 70 characters *and* costs more. So the body uses only
  the plain GSM-7 default alphabet: no curly quotes, no em dashes, no ``↔``
  ligature (all of which the email templates use happily).
* **No URL.** A link in a verification SMS trains users to tap links in SMS
  (the exact habit smishing relies on) and shortener-free URLs eat the segment
  budget. The user is already in the app; the code alone is enough.
* **The code is stated once, unmistakably.** It is the only actionable content.
"""

from src.application.dtos.sms_dto import SmsMessage

#: Single GSM-7 segment budget. Hard limit, not a target.
MAX_SMS_SEGMENT_CHARS = 160

#: GSM-7 default alphabet, plus the characters that are representable via the
#: escape mechanism (``^{}\[~]|€``) which cost two septets each. Anything
#: outside this set forces the whole message to UCS-2, halving the budget, so
#: the builder rejects it rather than quietly shrinking the limit.
_GSM7_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_EXTENDED = "^{}\\[~]|€"
_GSM7_CHARS = frozenset(_GSM7_BASIC + _GSM7_EXTENDED)


def is_gsm7(text: str) -> bool:
    """True when every character in ``text`` is representable in GSM-7."""
    return all(char in _GSM7_CHARS for char in text)


def build_phone_verification_sms(
    *,
    to: str,
    code: str,
    ttl_minutes: int,
    app_name: str = "Transform",
) -> SmsMessage:
    """Render the phone-verification SMS.

    ``app_name`` prefixes the body so the recipient can tell which service sent
    it without recognising a bare 6-digit code — the single most common source
    of "is this spam?" support tickets for SMS codes.
    """
    minutes = f"{ttl_minutes} minute" + ("" if ttl_minutes == 1 else "s")
    body = (
        f"{app_name}: your verification code is {code}. "
        f"It expires in {minutes}. Never share this code."
    )

    # Fail loudly at build time rather than silently truncating at the
    # transport or paying for a two-part message.
    if len(body) > MAX_SMS_SEGMENT_CHARS:
        raise ValueError(
            f"phone-verification SMS is {len(body)} characters, over the "
            f"{MAX_SMS_SEGMENT_CHARS}-character single-segment limit."
        )
    if not is_gsm7(body):
        raise ValueError(
            "phone-verification SMS contains characters outside the GSM-7 "
            "alphabet, which would force UCS-2 encoding and halve the segment."
        )

    return SmsMessage(to=to, body=body)
