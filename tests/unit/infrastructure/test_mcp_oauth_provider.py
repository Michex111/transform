"""The OAuth provider's diagnostics.

A refused token grant used to be invisible server-side: the SDK answers 400 and
the access log records only the status. That is not enough to tell "the client
presented a rotated token" from "the user revoked the grant", and it is exactly
the position an operator was in when a major client silently disconnected.
"""

from __future__ import annotations

import logging

from src.application.exceptions.mcp_exceptions import InvalidGrantError
from src.infrastructure.adapters.security.mcp_oauth_provider import (
    MCPOAuthProvider,
    _fingerprint,
)


def test_a_refused_grant_is_logged_with_its_reason(caplog) -> None:
    exc = InvalidGrantError("The refresh token is expired or revoked.")
    with caplog.at_level(logging.WARNING):
        error = MCPOAuthProvider._token_error(exc)

    assert error.error == "invalid_grant"
    assert error.error_description == "The refresh token is expired or revoked."
    assert any("MCP token grant refused" in record.message for record in caplog.records)
    assert "expired or revoked" in caplog.text


def test_a_token_fingerprint_cannot_be_used_as_the_token() -> None:
    """The diagnostic tag must be non-reversible, not a prefix of the secret."""

    token = "super-secret-refresh-token"
    fingerprint = _fingerprint(token)
    assert token not in fingerprint
    assert len(fingerprint) == 12
    assert _fingerprint(token) == _fingerprint(token)
    assert _fingerprint(token) != _fingerprint(token + "x")
