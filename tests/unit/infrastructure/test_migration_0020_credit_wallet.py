"""The ``0020_credit_wallet`` migration: columns, defaults, idempotency.

Pins the properties that turn a migration mistake into an *outage* rather than
a bug:

1. Every column the model expects must exist afterwards, or the API boots and
   then fails on the first credit read.
2. The three non-nullable columns must carry a ``server_default``, or
   ``ADD COLUMN`` against the populated production table fails outright.
3. A re-run must be a no-op: ``RUN_MIGRATIONS=true`` makes a migration error a
   *startup* failure, so a database where the columns were added out of band
   (or whose alembic stamp was reset) must not abort the boot on a
   duplicate-column error.
4. ``downgrade`` must remove everything ``upgrade`` created, so the revision is
   genuinely reversible.

The defaults are also behavioural: a fresh row must read as "no purchased
credits, no carryover, plan-first" so that adding this feature changes no
existing user's balance.
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
    / "0020_credit_wallet.py"
)

_NEW_COLUMNS = {
    "purchased_credits",
    "carryover_credits",
    "carryover_expires_at",
    "purchased_credits_first",
}

_CREATE_TABLE = """
CREATE TABLE user_subscriptions (
    actor_key VARCHAR(255) PRIMARY KEY,
    user_id INTEGER,
    tier VARCHAR(20) NOT NULL,
    used_storage_bytes BIGINT NOT NULL DEFAULT 0,
    stripe_customer_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0020", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine_with_subscriptions_table() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))
    return engine


def _columns(engine: sa.Engine) -> set[str]:
    return {col["name"] for col in sa.inspect(engine).get_columns("user_subscriptions")}


def _run(engine: sa.Engine, migration, direction: str) -> None:
    """Execute ``upgrade``/``downgrade`` against a real connection.

    The migration module reaches for the alembic ``op`` proxy as a module
    global, so it is bound to a live :class:`Operations` here rather than being
    imported.
    """
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        getattr(migration, direction)()


def test_upgrade_adds_every_column_the_model_expects(migration) -> None:
    engine = _engine_with_subscriptions_table()
    _run(engine, migration, "upgrade")

    assert _NEW_COLUMNS <= _columns(engine)


def test_upgrade_is_idempotent(migration) -> None:
    """Running twice must not raise — a duplicate column would be a boot failure."""
    engine = _engine_with_subscriptions_table()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "upgrade")  # must not raise

    assert _NEW_COLUMNS <= _columns(engine)


def test_downgrade_removes_everything_upgrade_added(migration) -> None:
    engine = _engine_with_subscriptions_table()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")

    assert not (_NEW_COLUMNS & _columns(engine))
    # The pre-existing columns must survive the rollback.
    assert "stripe_subscription_id" in _columns(engine)


def test_downgrade_is_idempotent(migration) -> None:
    engine = _engine_with_subscriptions_table()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")
    _run(engine, migration, "downgrade")  # must not raise


def test_existing_rows_default_to_zero_and_plan_first(migration) -> None:
    """The migration must not change any existing account's balance.

    A row that predates the column has to read as "no purchased credits, no
    carryover", because the whole point of the additive schema is that it is
    inert until the consumption path opts in.
    """
    engine = _engine_with_subscriptions_table()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO user_subscriptions "
                "(actor_key, tier, created_at, updated_at) "
                "VALUES ('user:1', 'FREE', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )

    _run(engine, migration, "upgrade")

    with engine.begin() as connection:
        row = connection.execute(
            sa.text(
                "SELECT purchased_credits, carryover_credits, "
                "carryover_expires_at, purchased_credits_first "
                "FROM user_subscriptions WHERE actor_key = 'user:1'"
            )
        ).one()

    assert row[0] == 0
    assert row[1] == 0
    assert row[2] is None
    assert row[3] in (0, False)
