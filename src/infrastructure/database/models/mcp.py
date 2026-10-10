"""ORM models for the MCP (agent access) OAuth server.

Four tables, one per OAuth concern:

* ``mcp_oauth_clients`` — dynamically registered MCP clients (the AI apps).
* ``mcp_agent_grants``  — a user's standing consent for one client. This is the
  row the "Connected apps" screen lists and revokes, and the row every MCP
  request is authorized against.
* ``mcp_oauth_codes``   — short-lived, single-use authorization codes (PKCE).
* ``mcp_oauth_tokens``  — access and refresh tokens, stored as SHA-256 hashes.

WHY tokens are opaque rows rather than JWTs: the SDK's
``OAuthAuthorizationServerProvider.load_access_token`` is called on every MCP
request, so a database lookup is already required. Making that lookup the
*authority* (instead of trusting a self-describing JWT) is what makes
revocation immediate — deleting/revoking the grant column stops the very next
call, with no window where a signed token still works.

Nothing here stores a raw credential: access tokens, refresh tokens and
authorization codes are persisted only as hashes.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.session import Base

# The ``status``/``kind`` columns below are plain ``String`` rather than a SQL
# ``Enum``. A new PostgreSQL enum type has to be created with
# ``checkfirst=True`` + ``create_type=False`` or ``upgrade head`` aborts with
# DuplicateObjectError on a database whose enum type outlived its tables (see
# ``test_migration_idempotency.py``). The values are only ever written and read
# through the domain enums in the repository, which coerce an unknown string to
# a fail-closed default — so nothing is lost by keeping the column a string.


class MCPOAuthClientModel(Base):
    """A dynamically registered OAuth client (an AI agent application)."""

    __tablename__ = "mcp_oauth_clients"

    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    #: Only present for confidential clients. MCP clients are usually public
    #: (``token_endpoint_auth_method = "none"``) and use PKCE instead. Stored
    #: verbatim because the SDK's ClientAuthenticator compares the presented
    #: secret against this value with a constant-time comparison.
    client_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    redirect_uris: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    grant_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    response_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_endpoint_auth_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class MCPAgentGrantModel(Base):
    """Standing user consent for one client — the revocable unit of access."""

    __tablename__ = "mcp_agent_grants"
    __table_args__ = (
        # One grant per (user, client): re-consenting updates the existing row
        # rather than accumulating duplicates on the Connected-apps screen.
        UniqueConstraint("user_id", "client_id", name="uq_mcp_grant_user_client"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    #: Denormalised so the Connected-apps list renders without a join, and so a
    #: deleted client row cannot leave an unnameable grant behind.
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Space-delimited scope set (RFC 6749 wire form). Parsed through
    #: ``normalize_scopes`` on read, so an unrecognised value can never be
    #: honoured even if it somehow reached the column.
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: RFC 8707 resource indicator this consent was given for (our MCP URL).
    resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: ``ALL`` or ``FOLDER``. A plain string for the same reason as ``status``
    #: above, and read through ``coerce_folder_access`` so an unreadable value
    #: fails closed to the restricted reading rather than to full access.
    folder_access: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="ALL"
    )
    #: The folder a ``FOLDER`` grant is confined to.
    #:
    #: Deliberately **not** a foreign key, for two reasons. A constraint cannot
    #: be added to an existing SQLite table by ``ALTER`` at all, so one here
    #: would put the model and the migration out of step; and the constraint
    #: would buy nothing, because enforcement already denies whenever the
    #: referenced folder cannot be resolved (see ``folder_scope_is_usable``). A
    #: hard ``ON DELETE SET NULL`` would additionally couple the grant's
    #: lifecycle to the folder's, which is the opposite of what we want: the
    #: user's record of what they consented to should outlive a folder they
    #: tidied up, and the denial is what keeps that safe.
    folder_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``AGENT`` or ``ALL`` — how much conversion history this grant may read.
    history_scope: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="AGENT"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When the user last suspended this connection, cleared on resume. The
    #: ``status`` column carries the authoritative state; this is the audit
    #: timestamp beside it (and lets the UI say *when* an agent was paused).
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MCPAuthorizationCodeModel(Base):
    """A single-use PKCE authorization code."""

    __tablename__ = "mcp_oauth_codes"

    #: SHA-256 of the code. The plaintext exists only inside the redirect the
    #: browser follows, so a database read cannot be replayed as a login.
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    code_challenge: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Non-null once redeemed. Kept (rather than deleting the row) so a replayed
    #: code is detectable and can be audited instead of looking like a typo.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MCPTokenModel(Base):
    """An issued access or refresh token, identified by the hash of its value."""

    __tablename__ = "mcp_oauth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
