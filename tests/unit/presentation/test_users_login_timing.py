"""Tests for the login timing-equalisation dummy hash.

A login attempt for a username that does not exist must still pay one Argon2
verification (so response time cannot be used to enumerate accounts). That
requires the dummy hash to be a well-formed Argon2 hash: a malformed one would
raise inside ``verify_password`` and turn the missing-user branch into a 500.
"""

from src.infrastructure.auth.jwt_provider import verify_password
from src.presentation.api.routers.v1.users import _DUMMY_PASSWORD_HASH


def test_dummy_hash_is_valid_and_never_matches() -> None:
    # Must not raise.
    assert verify_password("anything", _DUMMY_PASSWORD_HASH) is False
    assert verify_password("", _DUMMY_PASSWORD_HASH) is False
