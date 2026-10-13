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
    """A requested scope is not one this server may grant.

    Three distinct refusals share this error type, and they need different
    sentences, because they call for different actions:

    * the scope does not exist  → the client is broken, or the user asked for
      something the product does not offer;
    * the deployment forbids it → an operator decision, nothing the user can fix;
    * the *client* never registered for it → the client must register for it (or
      reconnect), which the user CAN act on.

    The original single sentence said "Unsupported or disallowed scope(s)" for
    all three. When a client that had omitted ``scope`` at registration asked
    for workspace access, that message read as "this product has no workspace
    support" — while the server was advertising five scopes and serving nine
    workspace tools. The cause was entirely in the client's own registration.
    """

    def __init__(self, scopes: object, *, reason: str = "unknown") -> None:
        listed = " ".join(str(s) for s in scopes) if isinstance(scopes, (list, tuple, set)) else str(scopes)
        if reason == "client":
            detail = (
                f"The application did not register for: {listed}. It must request these "
                "scopes when it registers with this server; reconnecting it will repeat "
                "the registration."
            )
        elif reason == "deployment":
            detail = (
                f"This deployment does not grant: {listed}. That is an operator setting, "
                "not something you can change by reconnecting."
            )
        elif reason == "unknown":
            detail = (
                f"Unknown scope(s): {listed}. Supported scopes are documents.read, "
                "documents.convert, documents.write, documents.delete, workspaces.read "
                "and workspaces.execute."
            )
        else:
            # Callers that predate the reason argument keep the original wording.
            detail = f"Unsupported or disallowed scope(s): {listed}."
        super().__init__("invalid_scope", detail)


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


class ConnectionEditError(MCPAccessError):
    """An in-place edit of a connection was refused because it is not a valid state.

    Two cases, both fixable by the user and both a 400 rather than a 404 (the
    connection exists and is the caller's):

    * removing **every** permission, which is a revocation rather than an edit;
    * enabling an irreversible permission (``documents.delete``) without an
      explicit confirmation.

    ``error`` is ``invalid_request``: the request is well-formed but asks for a
    state this server will not store.
    """

    def __init__(self, description: str) -> None:
        super().__init__("invalid_request", description)
