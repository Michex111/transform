"""The ``0016_user_profile_phone`` migration: columns, indexes, idempotency.

The properties worth pinning are the ones that turn a migration mistake into an
*outage* rather than a bug:

1. Every column the models expect must exist afterwards, or the API boots and
   then fails on the first profile read.
2. A re-run must be a no-op. ``RUN_MIGRATIONS=true`` makes a migration error a
   startup failure, so a database where the columns were added out of band (or
   whose alembic stamp was reset) must not abort the boot on a
   duplicate-column error.
3. ``downgrade`` must remove everything ``upgrade`` created — including both
   indexes — so the revision is genuinely reversible.
4. The unique index on ``phone_number`` must tolerate the many NULLs that every
   pre-existing account has, or adding it to a live table fails.
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
    / "0016_user_profile_phone.py"
)

_PHONE_INDEX = "uq_users_phone_number"
_CODE_HASH_INDEX = "ix_users_phone_verification_code_hash"

_NEW_COLUMNS = {
    "first_name",
    "last_name",
    "avatar_data",
    "avatar_content_type",
    "avatar_updated_at",
    "phone_number",
    "phone_verified",
    "phone_verified_at",
    "phone_verification_code_hash",
    "phone_verification_sent_at",
    "phone_verification_expires_at",
    "phone_verification_attempts",
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
    spec = importlib.util.spec_from_file_location("migration_0016", _MIGRATION_PATH)
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


def test_upgrade_adds_every_column_the_models_expect(migration) -> None:
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)

    assert _NEW_COLUMNS <= _columns(engine)


def test_upgrade_creates_a_unique_phone_index_and_a_code_lookup_index(migration) -> None:
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)

    indexes = _indexes(engine)
    # Unique, because a verified number must identify exactly one account. The
    # lookup-by-digest index is what keeps verification a single indexed read.
    assert indexes.get(_PHONE_INDEX) is True
    assert _CODE_HASH_INDEX in indexes
    assert indexes.get(_CODE_HASH_INDEX) is False


def test_the_unique_phone_index_tolerates_many_nulls(migration) -> None:
    """Every pre-existing account has a NULL number, so this must not collide."""
    engine = _engine_with_users_table()
    _seed_user(engine, "ada")
    _seed_user(engine, "bob")

    _run(engine, migration.upgrade)

    with engine.begin() as connection:
        # Two NULLs coexist...
        rows = connection.execute(sa.text("SELECT phone_number FROM users")).all()
        assert [row[0] for row in rows] == [None, None]
        # ...but a duplicate real number does not.
        connection.execute(sa.text("UPDATE users SET phone_number = '+14155552671' WHERE username = 'ada'"))
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE users SET phone_number = '+14155552671' WHERE username = 'bob'")
            )


def test_existing_rows_get_safe_defaults_for_the_not_null_columns(migration) -> None:
    """``phone_verified`` false / ``attempts`` 0 — never NULL, never verified."""
    engine = _engine_with_users_table()
    _seed_user(engine, "legacy")

    _run(engine, migration.upgrade)

    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT phone_verified, phone_verification_attempts FROM users")
        ).one()

    assert row[0] in (False, 0)  # SQLite returns the boolean as an int
    assert row[1] == 0


def test_upgrade_is_a_no_op_when_the_columns_already_exist(migration) -> None:
    """The out-of-band-applied case: an `op.add_column` would abort the boot."""
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.upgrade)

    assert _NEW_COLUMNS <= _columns(engine)
    assert _PHONE_INDEX in _indexes(engine)


def test_upgrade_tolerates_a_missing_index(migration) -> None:
    """Columns present but an index lost (e.g. a manual drop) must self-heal."""
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    with engine.begin() as connection:
        connection.execute(sa.text(f"DROP INDEX {_PHONE_INDEX}"))

    _run(engine, migration.upgrade)

    assert _PHONE_INDEX in _indexes(engine)


def test_downgrade_removes_the_columns_and_both_indexes(migration) -> None:
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)

    assert not (_NEW_COLUMNS & _columns(engine))
    assert _PHONE_INDEX not in _indexes(engine)
    assert _CODE_HASH_INDEX not in _indexes(engine)


def test_downgrade_is_idempotent(migration) -> None:
    """A partially-applied downgrade must not fail on the second attempt."""
    engine = _engine_with_users_table()

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)
    _run(engine, migration.downgrade)

    assert not (_NEW_COLUMNS & _columns(engine))
