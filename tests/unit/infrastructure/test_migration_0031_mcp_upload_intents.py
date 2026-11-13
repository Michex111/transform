"""The ``0031_mcp_upload_intents`` migration: table, indexes, idempotency.

The migration is guarded because ``RUN_MIGRATIONS=true`` turns a migration error
into a startup failure. A database where the table was created out of band must
converge instead of aborting the boot, and a partially-applied environment must
be able to run ``upgrade`` again.

The table is what makes an attachment upload a first-class, auditable action:
one row per authorized upload, with the grant, destination, size ceiling and
optional conversion target. It deliberately stores no capability and no storage
key.
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
    / "0031_mcp_upload_intents.py"
)

_TABLE = "mcp_upload_intents"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0031", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        # The table references users.id; the migration test does not need the
        # real users table, only a target for the foreign key.
        connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    return engine


def _columns(engine: sa.Engine) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns(_TABLE)}


def _indexes(engine: sa.Engine) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(engine).get_indexes(_TABLE)
        if index["name"]
    }


def _run(engine: sa.Engine, migration, direction: str) -> None:
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)
        getattr(migration, direction)()


def test_upgrade_creates_the_table_and_its_indexes() -> None:
    migration = _load_migration()
    engine = _engine()
    _run(engine, migration, "upgrade")

    assert {
        "id",
        "user_id",
        "grant_id",
        "client_id",
        "file_name",
        "extension",
        "max_bytes",
        "folder_id",
        "target_format",
        "status",
        "file_id",
        "job_id",
        "error",
        "created_at",
        "expires_at",
        "used_at",
    } <= _columns(engine)
    assert {
        "ix_mcp_upload_intents_user_created",
        "ix_mcp_upload_intents_expires_at",
        "ix_mcp_upload_intents_grant_id",
    } <= _indexes(engine)


def test_upgrade_is_idempotent() -> None:
    migration = _load_migration()
    engine = _engine()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "upgrade")  # must not raise
    assert "used_at" in _columns(engine)


def test_downgrade_drops_the_table() -> None:
    migration = _load_migration()
    engine = _engine()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")
    assert _TABLE not in sa.inspect(engine).get_table_names()
