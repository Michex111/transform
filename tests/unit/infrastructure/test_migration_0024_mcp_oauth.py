"""The ``0024_mcp_oauth`` migration: tables, indexes, idempotency, reversal.

Pins the properties that turn a migration mistake into an *outage* rather than a
bug:

1. All four MCP tables exist afterwards, or the API boots and then fails on the
   first OAuth request.
2. The grant table carries its uniqueness constraint and its indexes, because
   the registered agent's hot path is a lookup by ``(user_id, client_id)``.
3. A re-run is a no-op: ``RUN_MIGRATIONS=true`` makes a migration error a
   *startup* failure, so a database where the tables were created out of band
   must not abort the boot.
4. ``downgrade`` removes every table, so the revision is genuinely reversible —
   in dependency-safe order (the child tables before the grant they reference
   by convention, even though the columns are plain strings).
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
    / "0024_mcp_oauth.py"
)

_TABLES = (
    "mcp_oauth_clients",
    "mcp_agent_grants",
    "mcp_oauth_codes",
    "mcp_oauth_tokens",
)

#: The grant table has a foreign key to ``users``, so the target table must
#: exist before the migration runs.
_CREATE_USERS = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY
)
"""


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0024", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_USERS))
    return engine


def _table_names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _run(engine: sa.Engine, migration, direction: str) -> None:
    with engine.begin() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"transaction_per_migration": True, "transactional_ddl": True},
        )
        migration.op = Operations(context)
        getattr(migration, direction)()


def test_upgrade_creates_every_mcp_table(migration) -> None:
    engine = _engine()
    _run(engine, migration, "upgrade")

    assert set(_TABLES) <= _table_names(engine)
    # The credentials are stored as hashes, so the identity columns are the
    # hash — a schema where the raw value is the key would be a red flag.
    code_columns = {c["name"] for c in sa.inspect(engine).get_columns("mcp_oauth_codes")}
    token_columns = {c["name"] for c in sa.inspect(engine).get_columns("mcp_oauth_tokens")}
    assert "code_hash" in code_columns
    assert "token_hash" in token_columns
    # A raw credential column must not exist anywhere in the schema.
    for table in _TABLES:
        for column in sa.inspect(engine).get_columns(table):
            assert column["name"] not in {"code", "token", "access_token", "refresh_token"}


def test_grant_table_is_unique_per_user_and_client(migration) -> None:
    engine = _engine()
    _run(engine, migration, "upgrade")

    unique = {
        tuple(sorted(constraint["column_names"]))
        for constraint in sa.inspect(engine).get_unique_constraints("mcp_agent_grants")
    }
    assert ("client_id", "user_id") in unique

    indexed = {
        index["name"] for index in sa.inspect(engine).get_indexes("mcp_agent_grants")
    }
    assert {"ix_mcp_agent_grants_user_id", "ix_mcp_agent_grants_client_id"} <= indexed


def test_upgrade_is_idempotent(migration) -> None:
    """A second run must not raise: `RUN_MIGRATIONS=true` makes it a boot failure."""
    engine = _engine()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "upgrade")

    assert set(_TABLES) <= _table_names(engine)


def test_downgrade_removes_every_table(migration) -> None:
    engine = _engine()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")

    assert not (set(_TABLES) & _table_names(engine))
    # The users table the grant referenced must survive: dropping it would be a
    # catastrophic downgrade.
    assert "users" in _table_names(engine)
