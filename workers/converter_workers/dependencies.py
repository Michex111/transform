from src.infrastructure.adapters.queues.redis_stream_job_queue import JobStreamConsumer
from src.infrastructure.adapters.queues.redis_stream_status_queue import JobEventPublisher
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository

from datetime import UTC, datetime
from src.infrastructure.adapters.security.encryption import FileEncryptionService, get_file_encryption_service
from workers.converter_workers.ports import CreditPort, JobEventPort, JobRepositoryPort, QueuePort
from src.infrastructure.redis.client import create_redis_client
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_session_factory
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.policies.credit_wallet import (
    WalletBalances,
    available_total,
    consume_from_wallet,
)
from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import ensure_utc
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.job_origin import JobOrigin
from sqlalchemy.exc import IntegrityError


from src.domain.conversions.entities.conversion_job import ConversionJob
from sqlalchemy.exc import IntegrityError, OperationalError, DBAPIError


# Exceptions that indicate the DB connection was dropped (e.g. Neon's pooler
# closed an idle connection). These are transient and safely retried with a
# fresh session.
_RETRYABLE_DB_ERRORS = (OperationalError, DBAPIError, ConnectionError, OSError)


class WorkerJobRepository(JobRepositoryPort):
    """Persists job status transitions using a fresh DB session per update."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def update_conversion_job(self, job: ConversionJob) -> None:
        for attempt in range(2):
            try:
                async with self._session_factory() as session:
                    await SQLConversionJobRepository(session=session).update_conversion_job(job)
                return
            except _RETRYABLE_DB_ERRORS:
                # Connection was dropped (Neon pooler recycle / transient).
                # Retry once on a fresh session; the engine's pool_pre_ping will
                # also validate the new connection.
                if attempt == 1:
                    raise
                continue

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        """Read a job row, retrying once on a dropped connection.

        The processor uses this to decide whether a redelivered message has
        already been completed, so it must fail closed only on real errors.
        """
        for attempt in range(2):
            try:
                async with self._session_factory() as session:
                    return await SQLConversionJobRepository(session=session).get_conversion_job(job_id)
            except _RETRYABLE_DB_ERRORS:
                if attempt == 1:
                    raise
                continue
        # Unreachable: the loop above always returns or raises.
        raise RuntimeError("unreachable: job read loop exhausted")


class WorkerCreditRepository(CreditPort):
    """Resolves tiers and atomically consumes credits for the worker.

    Each call uses a fresh DB session (mirroring ``WorkerJobRepository``). A
    best-effort retry guards against a race on the unique ``(owner_id,
    period_key)`` constraint when two workers initialize the same bucket at
    once.

    Consumption spans the full wallet: the plan bucket lives in
    ``monthly_credits`` while carryover and purchased credits live on the
    ``user_subscriptions`` row, so both are loaded in the *same* session and
    written in a single commit. Splitting them across two commits would allow a
    failure between them to spend carryover without recording it.
    """

    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def get_remaining(self, user_id: int, period_key: str) -> int | None:
        for attempt in range(2):
            try:
                async with self._session_factory() as session:
                    credit = await SQLCreditRepository(session=session).get_credit(
                        str(user_id), period_key
                    )
                    return credit.remaining if credit is not None else None
            except _RETRYABLE_DB_ERRORS:
                if attempt == 1:
                    raise
                continue

    async def get_available_total(self, user_id: int, period_key: str) -> int | None:
        """Wallet-wide spendable total, for the pre-check gate.

        Origin-independent by construction: the pre-check answers "can this user
        afford a conversion at all?", and only the *order* pools are spent in
        depends on the job's origin. Gating on ``get_remaining`` (the plan
        bucket alone) would refuse an API user who has spent their plan credits
        but still holds purchased ones.
        """
        for attempt in range(2):
            try:
                async with self._session_factory() as session:
                    return await self._available_total_once(session, user_id, period_key)
            except _RETRYABLE_DB_ERRORS:
                if attempt == 1:
                    raise
                continue
        # Unreachable: the loop above always returns or raises.
        raise RuntimeError("unreachable: available-total loop exhausted")

    async def _available_total_once(
        self, session, user_id: int, period_key: str
    ) -> int | None:
        credit_repo = SQLCreditRepository(session=session)
        sub_repo = SQLSubscriptionRepository(session=session)

        credit = await credit_repo.get_credit(str(user_id), period_key)
        if credit is None:
            # No bucket yet: mirror ``consume``, which initializes the plan
            # bucket from the tier grant on first use. A tier with no finite
            # grant (Enterprise) has nothing to gate on, so return None.
            tier = await sub_repo.get_tier_for_user(user_id)
            grant = TierPolicy.for_tier(tier).monthly_conversion_credits
            if grant is None:
                return None
            plan_remaining = grant
        else:
            plan_remaining = credit.remaining

        wallet_row = await sub_repo.get_wallet(user_id)
        balances = WalletBalances(
            plan_remaining=plan_remaining,
            carryover_credits=wallet_row.carryover_credits if wallet_row is not None else 0,
            purchased_credits=wallet_row.purchased_credits if wallet_row is not None else 0,
        )
        return available_total(
            balances,
            carryover_expires_at=(
                ensure_utc(wallet_row.carryover_expires_at)
                if wallet_row is not None
                else None
            ),
            now=datetime.now(UTC),
        )

    async def get_tier(self, user_id: int) -> SubscriptionTier:
        for attempt in range(2):
            try:
                async with self._session_factory() as session:
                    return await SQLSubscriptionRepository(session=session).get_tier_for_user(user_id)
            except _RETRYABLE_DB_ERRORS:
                if attempt == 1:
                    raise
                continue
        # Unreachable: the loop above always returns or raises.
        raise RuntimeError("unreachable: credit tier resolution loop exhausted")

    async def consume(
        self,
        user_id: int,
        period_key: str,
        units: int,
        *,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> int:
        for attempt in range(2):
            try:
                async with self._session_factory() as session:
                    return await self._consume_once(
                        session, user_id, period_key, units, origin=origin, retried=False
                    )
            except _RETRYABLE_DB_ERRORS:
                if attempt == 1:
                    raise
                continue
        # Unreachable: the loop above always returns or raises.
        raise RuntimeError("unreachable: credit consume loop exhausted")

    async def _consume_once(
        self,
        session,
        user_id: int,
        period_key: str,
        units: int,
        *,
        origin: JobOrigin,
        retried: bool,
    ) -> int:
        credit_repo = SQLCreditRepository(session=session)
        sub_repo = SQLSubscriptionRepository(session=session)

        credit = await credit_repo.get_credit(str(user_id), period_key)
        if credit is None:
            tier = await sub_repo.get_tier_for_user(user_id)
            credit = Credit.from_tier(str(user_id), period_key, tier)

        wallet_row = await sub_repo.get_wallet(user_id)
        balances = WalletBalances(
            plan_remaining=credit.remaining,
            carryover_credits=wallet_row.carryover_credits if wallet_row is not None else 0,
            purchased_credits=wallet_row.purchased_credits if wallet_row is not None else 0,
        )

        # The spend ORDER alone depends on origin, and only for API jobs: a
        # browser session must never burn credits the user paid for ahead of
        # plan credits, while a programmatic caller who opted in may reserve
        # purchased credits for API usage. Carryover is always spent first
        # because it is the only pool that expires.
        prefer_purchased = (
            wallet_row is not None
            and wallet_row.purchased_credits_first
            and origin is JobOrigin.API
        )

        spent = consume_from_wallet(
            balances,
            units,
            carryover_expires_at=(
                ensure_utc(wallet_row.carryover_expires_at)
                if wallet_row is not None
                else None
            ),
            now=datetime.now(UTC),
            purchased_credits_first=prefer_purchased,
        )

        credit.remaining = spent.plan_remaining
        if wallet_row is not None:
            # Staged, not committed: ``save_credit`` below commits the session,
            # flushing both the plan bucket and this wallet row together.
            sub_repo.apply_wallet_balances(
                wallet_row,
                carryover_credits=spent.carryover_credits,
                purchased_credits=spent.purchased_credits,
            )

        try:
            await credit_repo.save_credit(credit)
        except IntegrityError:
            # Race: another worker inserted the same (owner_id, period_key)
            # bucket between our read and write. Roll back (this also undoes the
            # staged wallet mutation) and retry once — the bucket now exists, so
            # the retry takes the update path.
            if retried:
                raise
            await session.rollback()
            return await self._consume_once(
                session, user_id, period_key, units, origin=origin, retried=True
            )

        return credit.remaining


def get_job_repository() -> JobRepositoryPort:
    return WorkerJobRepository(session_factory=get_session_factory())


def get_credit_port() -> CreditPort:
    return WorkerCreditRepository(session_factory=get_session_factory())


def get_encryption_service() -> FileEncryptionService | None:
    """Return the at-rest encryption service, or None when not configured."""
    return get_file_encryption_service()


async def get_consumer_queue(consumer_group: str, consumer_name: str) -> QueuePort:
    settings = get_settings()
    client = create_redis_client(redis_url=settings.REDIS_URL.get_secret_value())

    return await JobStreamConsumer.create(
        consumer_group=consumer_group,
        consumer_name=consumer_name,
        redis_client=client
    )

def get_event_queue() -> JobEventPort:
    settings = get_settings()
    client = create_redis_client(redis_url=settings.REDIS_URL.get_secret_value())
    return JobEventPublisher(redis_client=client)