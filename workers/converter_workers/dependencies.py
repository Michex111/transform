from src.infrastructure.adapters.queues.redis_stream_job_queue import JobStreamConsumer
from src.infrastructure.adapters.queues.redis_stream_status_queue import JobEventPublisher
from src.application.ports.contracts import JobEventPort, QueuePort
from src.infrastructure.redis.client import create_redis_client
from src.infrastructure.config.settings import get_settings


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