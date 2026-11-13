"""The ``0030_mcp_authorization_core`` migration: columns, idempotency, reversal.

Two additive nullable columns:

* ``mcp_oauth_tokens.replaced_by`` — so a rotated refresh token's successor can
  be recognised without ever storing plaintext.
* ``conversion_jobs.grant_id`` — so the agent history tool can scope to one
  connection.

The migration is guarded (``_has_column``) because ``RUN_MIGRATIONS=true`` makes
a migration error a *startup* failure. A database where one column was added out
of band must not abort the boot.
"""

import importlib.util
from pathlib import Path

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
    / "0030_mcp_authorization_core.py"
)

_CREATE_TABLES = (
    "CREATE TABLE mcp_oauth_tokens (token_hash VARCHAR(64) PRIMARY KEY)",
    "CREATE TABLE conversion_jobs (job_id VARCHAR(64) PRIMARY KEY)",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0030", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in _CREATE_TABLES:
            connection.execute(sa.text(statement))
    return engine


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(table)}


def _run(engine: sa.Engine, migration, direction: str) -> None:
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)
        getattr(migration, direction)()


def test_upgrade_adds_both_columns_and_the_index() -> None:
    migration = _load_migration()
    engine = _engine()
    _run(engine, migration, "upgrade")

    assert "replaced_by" in _columns(engine, "mcp_oauth_tokens")
    assert "grant_id" in _columns(engine, "conversion_jobs")
    indexes = {index["name"] for index in sa.inspect(engine).get_indexes("conversion_jobs")}
    assert "ix_conversion_jobs_grant_id" in indexes


def test_upgrade_is_idempotent() -> None:
    migration = _load_migration()
    engine = _engine()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "upgrade")  # must not raise
    assert "grant_id" in _columns(engine, "conversion_jobs")


def test_downgrade_removes_what_it_added() -> None:
    migration = _load_migration()
    engine = _engine()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")
    assert "replaced_by" not in _columns(engine, "mcp_oauth_tokens")
    assert "grant_id" not in _columns(engine, "conversion_jobs")
