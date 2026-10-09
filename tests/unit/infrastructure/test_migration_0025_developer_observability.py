"""The ``0025_developer_observability`` migration: tables, column, idempotency, reversibility.

The properties worth pinning are the ones that turn a migration mistake into an
*outage* rather than a bug — ``RUN_MIGRATIONS=true`` makes any migration error a
startup failure on the production API:

1. Both tables and the ``paused_at`` column exist afterwards, with the indexes
   the dashboard's queries rely on.
2. A re-run is a no-op. A database whose tables were created out of band (or
   whose alembic stamp was reset) must not abort the boot with a
   duplicate-table error.
3. ``downgrade`` removes everything it created, so the revision is genuinely
   reversible.

The full chain cannot be run here — earlier revisions use PostgreSQL-only
``ALTER TYPE ... ADD VALUE`` — so the pre-migration schema is built by hand and
the migration callable is invoked directly through Alembic's op proxy. That is
the same technique the other single-migration tests in this folder use.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "database"
    / "migrations"
    / "versions"
    / "0025_developer_observability.py"
)

_NEW_TABLES = ("api_request_events", "mcp_tool_invocations")
_NEW_COLUMN = "paused_at"

#: The tables the migration references: two FK targets and the table that
#: receives the new column.
_PRE_EXISTING_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL
);
CREATE TABLE api_keys (
    id VARCHAR NOT NULL PRIMARY KEY,
    key VARCHAR(255) NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE mcp_agent_grants (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    client_id VARCHAR(255) NOT NULL,
    client_name VARCHAR(200) NOT NULL,
    scopes TEXT NOT NULL,
    status VARCHAR(16) NOT NULL,
    resource VARCHAR(500),
    created_at DATETIME NOT NULL,
    last_used_at DATETIME,
    revoked_at DATETIME
);
"""

#: Indexes the dashboard's queries depend on. Asserting them by name means a
#: future edit that drops one is a test failure rather than a slow query.
_EXPECTED_INDEXES = {
    "api_request_events": {
        "ix_api_request_events_account_time",
        "ix_api_request_events_account_key_time",
        "ix_api_request_events_account_status_time",
        "ix_api_request_events_request_id",
    },
    "mcp_tool_invocations": {
        "ix_mcp_tool_invocations_account_time",
        "ix_mcp_tool_invocations_account_grant_time",
        "ix_mcp_tool_invocations_account_tool",
        "ix_mcp_tool_invocations_account_outcome",
    },
}


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0025", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in _PRE_EXISTING_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.execute(sa.text(statement))
    return engine


def _run(engine: sa.Engine, fn) -> None:
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            fn()


def _tables(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return set(sa.inspect(connection).get_table_names())


def _indexes(engine: sa.Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        # `name` is typed optional by SQLAlchemy's inspector even though a real
        # index always has one, so the Nones are filtered rather than asserted
        # away.
        names = {index["name"] for index in sa.inspect(connection).get_indexes(table)}
    return {name for name in names if name}


def _columns(engine: sa.Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {col["name"] for col in sa.inspect(connection).get_columns(table)}


def test_upgrade_creates_both_tables_with_their_indexes(migration) -> None:
    engine = _engine()

    _run(engine, migration.upgrade)

    present = _tables(engine)
    for table in _NEW_TABLES:
        assert table in present
        assert _indexes(engine, table) == _EXPECTED_INDEXES[table]


def test_upgrade_adds_the_paused_at_column(migration) -> None:
    engine = _engine()

    _run(engine, migration.upgrade)

    # The authoritative state stays in the existing `status` VARCHAR; this is
    # only the audit timestamp beside it, which is why no enum migration is
    # needed anywhere.
    assert _NEW_COLUMN in _columns(engine, "mcp_agent_grants")
    assert "status" in _columns(engine, "mcp_agent_grants")


def test_a_second_upgrade_is_a_no_op(migration) -> None:
    """Re-running must be harmless: a migration error aborts the API's boot."""
    engine = _engine()

    _run(engine, migration.upgrade)
    _run(engine, migration.upgrade)  # must not raise duplicate-table/column

    assert _indexes(engine, "api_request_events") == _EXPECTED_INDEXES["api_request_events"]
    assert _NEW_COLUMN in _columns(engine, "mcp_agent_grants")


def test_upgrade_leaves_existing_grant_rows_untouched(migration) -> None:
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO mcp_agent_grants "
                "(id, user_id, client_id, client_name, scopes, status, created_at) "
                "VALUES ('g1', 1, 'client-1', 'Example Agent', 'documents.read', 'ACTIVE', "
                "'2026-01-01 00:00:00')"
            )
        )

    _run(engine, migration.upgrade)

    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT status, paused_at FROM mcp_agent_grants WHERE id = 'g1'")
        ).one()
    # Existing grants keep their state, and the new column is NULL — which is the
    # correct reading of "never paused".
    assert row[0] == "ACTIVE"
    assert row[1] is None


def test_downgrade_removes_everything_the_upgrade_added(migration) -> None:
    engine = _engine()
    _run(engine, migration.upgrade)

    _run(engine, migration.downgrade)

    present = _tables(engine)
    for table in _NEW_TABLES:
        assert table not in present
    assert _NEW_COLUMN not in _columns(engine, "mcp_agent_grants")
    # And it does not touch anything it did not create.
    assert "mcp_agent_grants" in present
