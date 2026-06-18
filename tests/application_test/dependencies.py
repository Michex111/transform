from src.infrastructure.adapters.storage.minio_storage import get_storage
from src.infrastructure.adapters.queue.redis_stream_queue import JobStream
from src.application.ports.queue_port import JobQueuePort
from redis.asyncio import Redis

def get_redis_queue(client: Redis) -> JobStream:
    return JobStream(client)

