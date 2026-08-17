from src.infrastructure.adapters.queues.redis_stream_job_queue import JobStreamConsumer
from src.infrastructure.adapters.queues.redis_stream_status_queue import JobEventPublisher
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.security.encryption import FileEncryptionService, get_file_encryption_service
from workers.converter_workers.ports import JobEventPort, JobRepositoryPort, QueuePort
from src.infrastructure.redis.client import create_redis_client
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_session_factory
from src.domain.conversions.entities.conversion_job import ConversionJob


class WorkerJobRepository(JobRepositoryPort):
    """Persists job status transitions using a fresh DB session per update."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def update_conversion_job(self, job: ConversionJob) -> None:
        async with self._session_factory() as session:
            await SQLConversionJobRepository(session=session).update_conversion_job(job)


def get_job_repository() -> JobRepositoryPort:
    return WorkerJobRepository(session_factory=get_session_factory())


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