"""The ``0022_backfill_purchased_credits`` data migration.

Unlike ``0020``/``0021`` this migration carries **data statements**, so the
properties that matter are behavioural rather than schema-shaped:

1. **Total conservation.** ``remaining`` before must equal
   ``plan_remaining + purchased_credits`` after. This is a re-labelling, never a
   grant or a clawback.
2. **The ledger caps the relabel.** A user who never bought a pack must have
   nothing moved — otherwise plan credits would be mislabelled as permanent
   purchased credits.
3. **Idempotency by construction.** After a run ``allowance == tier_grant``, so a
   re-run is a no-op. ``RUN_MIGRATIONS=true`` makes a migration error a startup
   failure, so this must hold.
4. **No-grant tiers are skipped.** ``GUEST``/``ENTERPRISE`` have no finite
   grant, so "grant + purchases" cannot be decomposed.
5. **Downgrade inverts it** without violating ``remaining <= allowance``.

The migration is loaded from its file (it is not a package) and bound to a live
alembic ``Operations``, exactly like the ``0020`` test.
"""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from src.domain.subscriptions.value_object.credit_period import current_period_key

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "database"
    / "migrations"
    / "versions"
    / "0022_backfill_purchased_credits.py"
)


def _engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE user_subscriptions ("
                "actor_key VARCHAR(255) PRIMARY KEY, "
                "user_id INTEGER, "
                "tier VARCHAR(20) NOT NULL, "
                "purchased_credits INTEGER NOT NULL DEFAULT 0)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE monthly_credits ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "owner_id VARCHAR(64) NOT NULL, "
                "period_key VARCHAR(7) NOT NULL, "
                "allowance INTEGER NOT NULL, "
                "remaining INTEGER NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE credit_transactions ("
                "id VARCHAR(64) PRIMARY KEY, "
                "user_id INTEGER NOT NULL, "
                "amount INTEGER NOT NULL, "
                "transaction_type VARCHAR(20) NOT NULL)"
            )
        )
    return engine


def _seed(
    engine: sa.Engine,
    *,
    user_id: int,
    tier: str,
    allowance: int,
    remaining: int,
    purchased: int = 0,
    ledger_purchases: int = 0,
    with_subscription: bool = True,
) -> None:
    period = current_period_key()
    with engine.begin() as connection:
        if with_subscription:
            connection.execute(
                sa.text(
                    "INSERT INTO user_subscriptions "
                    "(actor_key, user_id, tier, purchased_credits) "
                    "VALUES (:actor_key, :user_id, :tier, :purchased)"
                ),
                {
                    "actor_key": f"user:{user_id}",
                    "user_id": user_id,
                    "tier": tier,
                    "purchased": purchased,
                },
            )
        connection.execute(
            sa.text(
                "INSERT INTO monthly_credits "
                "(owner_id, period_key, allowance, remaining) "
                "VALUES (:owner_id, :period_key, :allowance, :remaining)"
            ),
            {
                "owner_id": str(user_id),
                "period_key": period,
                "allowance": allowance,
                "remaining": remaining,
            },
        )
        if ledger_purchases:
            connection.execute(
                sa.text(
                    "INSERT INTO credit_transactions "
                    "(id, user_id, amount, transaction_type) "
                    "VALUES (:id, :user_id, :amount, 'PURCHASE')"
                ),
                {
                    "id": f"tx-{user_id}",
                    "user_id": user_id,
                    "amount": ledger_purchases,
                },
            )


def _state(engine: sa.Engine, user_id: int) -> tuple[tuple[int, int], int]:
    period = current_period_key()
    with engine.begin() as connection:
        credit = connection.execute(
            sa.text(
                "SELECT allowance, remaining FROM monthly_credits "
                "WHERE owner_id = :owner_id AND period_key = :period_key"
            ),
            {"owner_id": str(user_id), "period_key": period},
        ).one()
        purchased = connection.execute(
            sa.text(
                "SELECT purchased_credits FROM user_subscriptions "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        ).scalar_one()
    return (int(credit[0]), int(credit[1])), int(purchased)


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0022", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine: sa.Engine, migration, direction: str) -> None:
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        getattr(migration, direction)()


def test_upgrade_conserves_the_total_and_relabels_the_purchased_portion() -> None:
    engine = _engine()
    # PRO grant is 500; allowance 1500 = 500 plan + 1000 bought.
    _seed(
        engine,
        user_id=1,
        tier="PRO",
        allowance=1500,
        remaining=1400,
        ledger_purchases=1000,
    )
    migration = _migration()

    _run(engine, migration, "upgrade")

    (allowance, remaining), purchased = _state(engine, 1)
    assert allowance == 500  # bucket reset to the tier grant (idempotent state)
    assert remaining == 400
    assert purchased == 1000
    # Conservation: before `remaining` == after plan + purchased.
    assert remaining + purchased == 1400


def test_ledger_cap_prevents_relabelling_a_non_buyer() -> None:
    """The single most important guard: no purchases ⇒ nothing may move."""
    engine = _engine()
    _seed(
        engine,
        user_id=2,
        tier="PRO",
        allowance=1500,  # looks inflated, but the user never bought anything
        remaining=1400,
        ledger_purchases=0,
    )
    migration = _migration()

    _run(engine, migration, "upgrade")

    (allowance, remaining), purchased = _state(engine, 2)
    assert (allowance, remaining) == (1500, 1400)
    assert purchased == 0


def test_upgrade_is_idempotent() -> None:
    engine = _engine()
    _seed(
        engine,
        user_id=3,
        tier="PRO",
        allowance=1500,
        remaining=1400,
        ledger_purchases=1000,
    )
    migration = _migration()

    _run(engine, migration, "upgrade")
    first = _state(engine, 3)
    _run(engine, migration, "upgrade")  # must be a no-op

    assert _state(engine, 3) == first
    assert first == ((500, 400), 1000)


def test_tier_without_a_finite_grant_is_skipped() -> None:
    engine = _engine()
    _seed(
        engine,
        user_id=4,
        tier="ENTERPRISE",
        allowance=1500,
        remaining=1400,
        ledger_purchases=1000,
    )
    migration = _migration()

    _run(engine, migration, "upgrade")

    (allowance, remaining), purchased = _state(engine, 4)
    assert (allowance, remaining) == (1500, 1400)
    assert purchased == 0


def test_a_user_without_a_subscription_row_is_skipped() -> None:
    engine = _engine()
    _seed(
        engine,
        user_id=5,
        tier="PRO",
        allowance=1500,
        remaining=1400,
        ledger_purchases=1000,
        with_subscription=False,
    )
    migration = _migration()

    _run(engine, migration, "upgrade")

    with engine.begin() as connection:
        remaining = connection.execute(
            sa.text("SELECT remaining FROM monthly_credits WHERE owner_id = '5'")
        ).scalar_one()
    assert remaining == 1400


def test_downgrade_inverts_the_relabel() -> None:
    engine = _engine()
    _seed(
        engine,
        user_id=6,
        tier="PRO",
        allowance=1500,
        remaining=1400,
        ledger_purchases=1000,
    )
    migration = _migration()

    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")

    (allowance, remaining), purchased = _state(engine, 6)
    assert (allowance, remaining) == (1500, 1400)
    assert purchased == 0
    # The bucket invariant still holds after the fold-back.
    assert remaining <= allowance


def test_downgrade_is_idempotent() -> None:
    engine = _engine()
    _seed(
        engine,
        user_id=7,
        tier="PRO",
        allowance=1500,
        remaining=1400,
        ledger_purchases=1000,
    )
    migration = _migration()

    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")
    after_first = _state(engine, 7)
    _run(engine, migration, "downgrade")  # must be a no-op

    assert _state(engine, 7) == after_first
