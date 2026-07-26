import asyncio

from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.database.models import MonthlyCreditModel, UserSubscriptionModel


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self) -> None:
        self.subscriptions: dict[str, UserSubscriptionModel] = {}
        self.credits: dict[tuple[str, str], MonthlyCreditModel] = {}
        self.added: list[object] = []

    async def execute(self, stmt):
        where_values = list(stmt.compile().params.values())
        table_name = stmt.column_descriptions[0]["entity"].__tablename__

        if table_name == "user_subscriptions":
            actor_key = str(where_values[0])
            return _ScalarResult(self.subscriptions.get(actor_key))
        owner_id = str(where_values[0])
        period_key = str(where_values[1])
        return _ScalarResult(self.credits.get((owner_id, period_key)))

    def add(self, model):
        self.added.append(model)
        if isinstance(model, UserSubscriptionModel):
            self.subscriptions[model.actor_key] = model
        if isinstance(model, MonthlyCreditModel):
            self.credits[(model.owner_id, model.period_key)] = model

    async def commit(self) -> None:
        return None


def test_sql_subscription_repository_persists_usage_and_credits() -> None:
    async def run() -> None:
        session = _FakeSession()
        repo = SQLSubscriptionRepository(session)  # type: ignore[arg-type]

        await repo.set_used_storage_bytes("user:1", 2048)
        assert await repo.get_used_storage_bytes("user:1") == 2048
        assert await repo.get_actor_tier("user:1") == SubscriptionTier.GUEST

        credit = Credit(owner_id="1", period_key="2026-07", allowance=100, remaining=99)
        await repo.save_credit(credit)

        saved = await repo.get_credit("1", "2026-07")
        assert saved is not None
        assert saved.allowance == 100
        assert saved.remaining == 99

    asyncio.run(run())
