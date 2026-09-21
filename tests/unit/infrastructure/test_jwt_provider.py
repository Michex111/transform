"""Tests for JWT minting/verification (token-type confusion guard)."""

from datetime import UTC, datetime, timedelta

import jwt

from src.infrastructure.auth.jwt_provider import (
    create_access_token,
    create_refresh_token,
    verify_access_token,
    verify_refresh_token,
)
from src.infrastructure.config.settings import get_settings


def test_access_token_round_trips() -> None:
    token = create_access_token({"sub": "42"})
    assert verify_access_token(token) == "42"


def test_refresh_token_is_not_accepted_as_an_access_token() -> None:
    token = create_refresh_token({"sub": "42"})
    assert verify_access_token(token) is None


def test_access_token_is_not_accepted_as_a_refresh_token() -> None:
    token = create_access_token({"sub": "42"})
    assert verify_refresh_token(token) is None


def test_token_without_a_type_claim_is_rejected() -> None:
    """Every minted token sets ``type``; a hand-crafted one must not pass."""
    settings = get_settings()
    payload = {"sub": "42", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    token = jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.ALGORITHM,
    )
    assert verify_access_token(token) is None
