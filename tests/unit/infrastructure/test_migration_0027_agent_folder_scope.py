"""The ``0027_agent_folder_scope`` migration: columns, defaults, idempotency.

The properties worth pinning are the ones that turn a migration mistake into an
*outage* rather than a bug — ``RUN_MIGRATIONS=true`` makes any migration error a
startup failure on the production API:

1. All three consent parameters exist afterwards.
2. A re-run is a no-op, so a database migrated out of band (or with a reset
   alembic stamp) does not abort the boot with a duplicate-column error.
3. ``downgrade`` removes everything it created.
4. Existing rows land on ``ALL`` / ``AGENT``. The folder default is the *permissive*
   one on purpose — those grants were given under whole-Drive rules and must not
   be retroactively narrowed — while the history default is the narrow one,
   because that capability did not exist when they were granted.

The full chain cannot be run here (earlier revisions use PostgreSQL-only
``ALTER TYPE ... ADD VALUE``), so the pre-migration schema is built by hand and
the migration callable is invoked directly through Alembic's op proxy — the same
technique the other single-migration tests in this folder use.
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
    / "0027_agent_folder_scope.py"
)

_TABLE = "mcp_agent_grants"
_NEW_COLUMNS = ("folder_access", "folder_id", "history_scope")

#: The schema as it exists before this revision. ``mcp_agent_grants`` carries
#: exactly the columns 0024/0025 left behind — the new three must come only from
#: the migration under test, never from this fixture.
_PRE_EXISTING_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
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
    revoked_at DATETIME,
    paused_at DATETIME
);
"""


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0027", _MIGRATION_PATH)
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


def _columns(engine: sa.Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {col["name"] for col in sa.inspect(connection).get_columns(table)}


def _seed_grant(engine: sa.Engine, grant_id: str = "g1") -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (id, username, email, hashed_password, is_active, created_at)"
                " VALUES (1, 'ada', 'ada@example.com', 'x', 1, '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO mcp_agent_grants"
                " (id, user_id, client_id, client_name, scopes, status, created_at)"
                " VALUES (:id, 1, 'client-1', 'Claude', 'documents.read',"
                " 'ACTIVE', '2026-01-01 00:00:00')"
            ),
            {"id": grant_id},
        )


def test_upgrade_adds_the_three_consent_parameters(migration) -> None:
    engine = _engine()

    _run(engine, migration.upgrade)

    present = _columns(engine, _TABLE)
    for column in _NEW_COLUMNS:
        assert column in present, f"{column} missing after upgrade"
    # The pre-existing columns must survive: this migration is additive.
    assert "scopes" in present and "status" in present


def test_a_second_upgrade_is_a_no_op(migration) -> None:
    """Re-running must be harmless: a migration error aborts the API's boot."""
    engine = _engine()

    _run(engine, migration.upgrade)
    _run(engine, migration.upgrade)  # must not raise duplicate-column

    assert set(_NEW_COLUMNS).issubset(_columns(engine, _TABLE))


def test_existing_grants_land_on_whole_drive_and_agent_only_history(migration) -> None:
    """The backfill direction differs per column, and both are deliberate.

    ``folder_access='ALL'`` because these grants were consented to under
    whole-Drive rules — anything narrower would retroactively take away access
    the user actually approved. ``history_scope='AGENT'`` because the history
    capability did not exist when they were granted, so the narrow reading is
    the only honest starting point.
    """
    engine = _engine()
    _seed_grant(engine)

    _run(engine, migration.upgrade)

    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT folder_access, folder_id, history_scope FROM mcp_agent_grants")
        ).one()
    assert row[0] == "ALL"
    assert row[1] is None
    assert row[2] == "AGENT"


def test_downgrade_removes_the_three_columns(migration) -> None:
    engine = _engine()

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)

    present = _columns(engine, _TABLE)
    for column in _NEW_COLUMNS:
        assert column not in present, f"{column} survived downgrade"
    # Still a working table, not a dropped one.
    assert "scopes" in present
