"""The ``0018_user_default_save_folder`` migration: column, idempotency, reversibility.

The properties worth pinning are the ones that turn a migration mistake into an
*outage* rather than a bug:

1. The column the model expects must exist afterwards, or the API boots and then
   fails on the first profile read.
2. A re-run must be a no-op. ``RUN_MIGRATIONS=true`` makes a migration error a
   startup failure, so a database where the column was added out of band (or
   whose alembic stamp was reset) must not abort the boot on a
   duplicate-column error.
3. ``downgrade`` must remove the column, so the revision is genuinely
   reversible.
4. ``upgrade`` must not touch existing rows: NULL *is* the correct state for
   "save to root" on every pre-existing account, so there is deliberately no
   backfill.
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
    / "0018_user_default_save_folder.py"
)

_NEW_COLUMN = "default_save_folder_id"

_CREATE_TABLE = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0018", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine_with_users_table() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))
    return engine


def _run(engine: sa.Engine, fn) -> None:
    """Invoke a migration callable against ``engine`` via Alembic's op proxy."""
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            fn()


def _columns(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {col["name"] for col in sa.inspect(connection).get_columns("users")}


def _seed_user(engine: sa.Engine, username: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (username, email, hashed_password, is_active, created_at) "
                f"VALUES ('{username}', '{username}@example.com', 'x', 1, '2026-01-01 00:00:00')"
            )
        )


def _pre_existing_columns(engine: sa.Engine):
    """The original user columns, so a backfill would show up as a changed row."""
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT id, username, email, hashed_password, is_active, created_at "
                "FROM users ORDER BY id"
            )
        ).all()


def test_upgrade_adds_the_column_the_model_expects(migration) -> None:
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)

    assert _NEW_COLUMN in _columns(engine)


def test_upgrade_does_not_mutate_existing_rows(migration) -> None:
    """Deliberately no backfill: NULL correctly means "save to root"."""
    engine = _engine_with_users_table()
    _seed_user(engine, "legacy")
    before = _pre_existing_columns(engine)

    _run(engine, migration.upgrade)

    assert _pre_existing_columns(engine) == before
    with engine.connect() as connection:
        value = connection.execute(
            sa.text(f"SELECT {_NEW_COLUMN} FROM users")
        ).scalar_one()
    assert value is None


def test_upgrade_is_a_no_op_when_the_column_already_exists(migration) -> None:
    """The out-of-band-applied case: an `op.add_column` would abort the boot."""
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.upgrade)

    assert _NEW_COLUMN in _columns(engine)


def test_downgrade_removes_the_column(migration) -> None:
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)

    assert _NEW_COLUMN not in _columns(engine)


def test_downgrade_is_idempotent(migration) -> None:
    """A partially-applied downgrade must not fail on the second attempt."""
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)
    _run(engine, migration.downgrade)

    assert _NEW_COLUMN not in _columns(engine)
