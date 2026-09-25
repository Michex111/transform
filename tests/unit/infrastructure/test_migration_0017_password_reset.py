"""The ``0017_password_reset`` migration: columns, index, idempotency.

The properties worth pinning are the ones that turn a migration mistake into an
*outage* rather than a bug:

1. Every column the models expect must exist afterwards, or the API boots and
   then fails on the first reset request.
2. A re-run must be a no-op. ``RUN_MIGRATIONS=true`` makes a migration error a
   startup failure, so a database where the columns were added out of band (or
   whose alembic stamp was reset) must not abort the boot on a
   duplicate-column error.
3. ``downgrade`` must remove everything ``upgrade`` created, so the revision is
   genuinely reversible.
4. ``upgrade`` must not touch existing rows. Unlike ``0015`` there is
   deliberately no backfill: NULL *is* the correct state for "no reset in
   flight" on every pre-existing account.
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
    / "0017_password_reset.py"
)

_TOKEN_INDEX = "ix_users_password_reset_token_hash"

_NEW_COLUMNS = {
    "password_reset_token_hash",
    "password_reset_sent_at",
    "password_reset_expires_at",
}

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
    spec = importlib.util.spec_from_file_location("migration_0017", _MIGRATION_PATH)
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


def _indexes(engine: sa.Engine) -> dict[str, bool]:
    with engine.connect() as connection:
        return {
            idx["name"]: bool(idx.get("unique"))
            for idx in sa.inspect(connection).get_indexes("users")
        }


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


def test_upgrade_adds_every_column_the_models_expect(migration) -> None:
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)

    assert _NEW_COLUMNS <= _columns(engine)


def test_upgrade_creates_a_non_unique_lookup_index(migration) -> None:
    """The reset path looks the row up by digest, so this keeps it one read.

    Not unique: many accounts legitimately have no token in flight, so several
    NULLs must coexist — and two accounts *could* momentarily hold equal digests
    only if the tokens collided, which a 256-bit CSPRNG makes impossible.
    """
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)

    indexes = _indexes(engine)
    assert _TOKEN_INDEX in indexes
    assert indexes.get(_TOKEN_INDEX) is False


def test_upgrade_does_not_mutate_existing_rows(migration) -> None:
    """Deliberately no backfill: NULL correctly means "no reset in flight".

    ``0015`` had to write data because it introduced a sign-in gate; this
    revision introduces nothing that existing rows need grandfathering for.
    """
    engine = _engine_with_users_table()
    _seed_user(engine, "legacy")
    before = _pre_existing_columns(engine)

    _run(engine, migration.upgrade)

    assert _pre_existing_columns(engine) == before
    with engine.connect() as connection:
        reset_columns = connection.execute(
            sa.text(
                "SELECT password_reset_token_hash, password_reset_sent_at, "
                "password_reset_expires_at FROM users"
            )
        ).one()
    assert tuple(reset_columns) == (None, None, None)


def test_upgrade_is_a_no_op_when_the_columns_already_exist(migration) -> None:
    """The out-of-band-applied case: an `op.add_column` would abort the boot."""
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.upgrade)

    assert _NEW_COLUMNS <= _columns(engine)
    assert _TOKEN_INDEX in _indexes(engine)


def test_upgrade_tolerates_a_missing_index(migration) -> None:
    """Columns present but an index lost (e.g. a manual drop) must self-heal."""
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    with engine.begin() as connection:
        connection.execute(sa.text(f"DROP INDEX {_TOKEN_INDEX}"))

    _run(engine, migration.upgrade)

    assert _TOKEN_INDEX in _indexes(engine)


def test_downgrade_removes_the_columns_and_the_index(migration) -> None:
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)

    assert not (_NEW_COLUMNS & _columns(engine))
    assert _TOKEN_INDEX not in _indexes(engine)


def test_downgrade_is_idempotent(migration) -> None:
    """A partially-applied downgrade must not fail on the second attempt."""
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)
    _run(engine, migration.downgrade)

    assert not (_NEW_COLUMNS & _columns(engine))
