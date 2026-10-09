"""Exceptions raised by the MCP (agent access) application layer.

These are *domain-ish* failures of the authorization server: a redirect target
that the client did not register, a scope nobody may hand out, a code that was
already redeemed. They carry the RFC 6749 ``error`` code so the transport layer
can render a compliant response without re-deriving the reason — and so the
reason is stated once, in the layer that decides it.
"""


class MCPAccessError(Exception):
    """Base class for MCP authorization failures.

    ``error`` is the OAuth error code (``invalid_request`` / ``invalid_grant`` /
    ``invalid_scope`` / ``unauthorized_client`` / ``invalid_target``) and
    ``description`` is a short human sentence safe to show a developer. Neither
    ever contains a credential.
    """

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


class UnknownClientError(MCPAccessError):
    """The ``client_id`` is not registered here."""

    def __init__(self, client_id: str) -> None:
        super().__init__("invalid_request", f"Client ID '{client_id}' is not registered.")


class InvalidRedirectUriError(MCPAccessError):
    """The ``redirect_uri`` does not exactly match a registered value.

    Deliberately raised for *any* mismatch, including a near-miss (different
    port, different path). OAuth's redirect-URI matching is exact precisely so a
    weakened comparison cannot be used to exfiltrate an authorization code.
    """

    def __init__(self) -> None:
        super().__init__(
            "invalid_request",
            "The redirect_uri does not match any registered redirect URI for this client.",
        )


class InvalidScopeError(MCPAccessError):
    """A requested scope is not one this server may grant."""

    def __init__(self, scopes: object) -> None:
        super().__init__("invalid_scope", f"Unsupported or disallowed scope(s): {scopes}.")


class InvalidTargetError(MCPAccessError):
    """The RFC 8707 ``resource`` indicator is not this server.

    Accepting a token — or minting one — for another resource is the token
    passthrough / confused-deputy failure the MCP specification forbids
    outright, so a mismatched resource is a hard error rather than a warning.
    """

    def __init__(self, resource: str | None) -> None:
        super().__init__(
            "invalid_target",
            f"The requested resource {resource!r} is not this MCP server.",
        )


class InvalidGrantError(MCPAccessError):
    """A code or refresh token could not be redeemed."""

    def __init__(self, description: str = "The authorization grant is invalid, expired or revoked.") -> None:
        super().__init__("invalid_grant", description)


class GrantRevokedError(MCPAccessError):
    """The underlying user consent was withdrawn."""

    def __init__(self) -> None:
        super().__init__(
            "invalid_grant",
            "The user has revoked this application's access.",
        )


class ConnectionStateError(MCPAccessError):
    """A pause/resume was asked for on a grant whose state forbids it.

    The concrete case is a **revoked** grant: revocation is terminal, so it can
    be neither paused nor resumed. Returning ``invalid_request`` (a 400) rather
    than a 404 is deliberate — the connection exists and is the caller's, it is
    simply not in a state that can transition, and the UI should say so instead
    of implying the connection vanished.
    """

    def __init__(self, description: str) -> None:
        super().__init__("invalid_request", description)
