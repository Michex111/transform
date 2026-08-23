from src.infrastructure.adapters.queues.redis_stream_job_queue import JobStreamConsumer
from src.infrastructure.adapters.queues.redis_stream_status_queue import JobEventPublisher
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.adapters.security.encryption import FileEncryptionService, get_file_encryption_service
from workers.converter_workers.ports import CreditPort, JobEventPort, JobRepositoryPort, QueuePort
from src.infrastructure.redis.client import create_redis_client
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_session_factory
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.exceptions import InsufficientCredits
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.domain.conversions.entities.conversion_job import ConversionJob
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


class WorkerCreditRepository(CreditPort):
    """Resolves tiers and atomically consumes monthly credits for the worker.

    Each call uses a fresh DB session (mirroring ``WorkerJobRepository``). A
    best-effort retry guards against a race on the unique ``(owner_id,
    period_key)`` constraint when two workers initialize the same bucket at
    once.
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

    async def get_tier(self, user_id: int) -> SubscriptionTier:
        for attempt in range(2):
            try:
                async with self._session_factory() as session:
                    return await SQLSubscriptionRepository(session=session).get_tier_for_user(user_id)
            except _RETRYABLE_DB_ERRORS:
                if attempt == 1:
                    raise
                continue

    async def consume(self, user_id: int, period_key: str, units: int) -> int:
        for attempt in range(2):
            try:
                async with self._session_factory() as session:
                    return await self._consume_once(session, user_id, period_key, units, retried=False)
            except _RETRYABLE_DB_ERRORS:
                if attempt == 1:
                    raise
                continue

    async def _consume_once(
        self,
        session,
        user_id: int,
        period_key: str,
        units: int,
        *,
        retried: bool,
    ) -> int:
        repo = SQLCreditRepository(session=session)
        credit = await repo.get_credit(str(user_id), period_key)
        if credit is None:
            tier = await SQLSubscriptionRepository(session=session).get_tier_for_user(user_id)
            credit = Credit.from_tier(str(user_id), period_key, tier)

        try:
            credit.consume_for_conversion(units)
        except InsufficientCredits:
            # Clamp at the floor — never negative.
            credit.remaining = 0

        try:
            await repo.save_credit(credit)
        except IntegrityError:
            # Race: another worker inserted the same (owner_id, period_key)
            # bucket between our read and write. Roll back and retry once —
            # the bucket now exists, so the retry takes the update path.
            if retried:
                raise
            await session.rollback()
            return await self._consume_once(session, user_id, period_key, units, retried=True)

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