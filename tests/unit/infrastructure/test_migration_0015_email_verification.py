"""The ``0015_email_verification`` migration: grandfathering + idempotency.

Two properties are worth pinning, and both are data-loss / lockout classes
rather than cosmetic:

1. **Existing accounts must be grandfathered to verified.** The sign-in gate
   rejects unverified accounts, so leaving pre-existing rows at ``false`` would
   lock out every user who registered before this feature existed — permanently,
   because their addresses were never going to receive a token that was never
   sent.
2. **A re-run must not repeat that backfill.** The migration is idempotent
   (``RUN_MIGRATIONS=true`` makes a migration error a *startup* failure), so it
   can legitimately execute against a database where the columns already exist.
   If the backfill were unconditional, that second run would mark every
   genuinely-unverified account as verified, silently disabling the feature.
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
    / "0015_email_verification.py"
)
_INDEX = "ix_users_email_verification_token_hash"

_NEW_COLUMNS = {
    "email_verified",
    "email_verified_at",
    "email_verification_token_hash",
    "email_verification_sent_at",
    "email_verification_expires_at",
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
    spec = importlib.util.spec_from_file_location("migration_0015", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine: sa.Engine, fn) -> None:
    """Invoke a migration callable against ``engine`` via Alembic's op proxy."""
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            fn()


def _columns(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {col["name"] for col in sa.inspect(connection).get_columns("users")}


def _indexes(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {idx["name"] for idx in sa.inspect(connection).get_indexes("users")}


def _seed_user(engine: sa.Engine, username: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (username, email, hashed_password, is_active, created_at) "
                f"VALUES ('{username}', '{username}@example.com', 'x', 1, '2026-01-01 00:00:00')"
            )
        )


def _verified(engine: sa.Engine, username: str) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                sa.text("SELECT email_verified FROM users WHERE username = :u"),
                {"u": username},
            ).scalar()
        )


def test_upgrade_adds_every_column_and_the_lookup_index(migration) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))

    _run(engine, migration.upgrade)

    assert _NEW_COLUMNS <= _columns(engine)
    # The token is looked up BY digest, so the index is load-bearing.
    assert _INDEX in _indexes(engine)


def test_upgrade_grandfathers_pre_existing_accounts(migration) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))
    _seed_user(engine, "legacy")

    _run(engine, migration.upgrade)

    assert _verified(engine, "legacy") is True


def test_rerunning_upgrade_does_not_sweep_up_unverified_accounts(migration) -> None:
    """The failure this guards: a second run verifying accounts that must not be."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))
    _seed_user(engine, "legacy")

    _run(engine, migration.upgrade)

    # A signup that happened after the migration, still refusing to verify.
    _seed_user(engine, "newuser")
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE users SET email_verified = 0 WHERE username = 'newuser'"))

    _run(engine, migration.upgrade)

    assert _verified(engine, "newuser") is False
    assert _verified(engine, "legacy") is True


def test_upgrade_is_a_no_op_when_the_columns_already_exist(migration) -> None:
    """The out-of-band-applied case: an `op.add_column` would abort the boot."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))

    _run(engine, migration.upgrade)
    _run(engine, migration.upgrade)

    assert _NEW_COLUMNS <= _columns(engine)
    assert _INDEX in _indexes(engine)


def test_upgrade_tolerates_a_missing_index(migration) -> None:
    """Columns present but the index lost (e.g. a manual drop) must self-heal."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))

    _run(engine, migration.upgrade)
    with engine.begin() as connection:
        connection.execute(sa.text(f"DROP INDEX {_INDEX}"))

    _run(engine, migration.upgrade)

    assert _INDEX in _indexes(engine)


def test_downgrade_removes_the_columns_and_index(migration) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)

    assert not (_NEW_COLUMNS & _columns(engine))
    assert _INDEX not in _indexes(engine)
