from redis.asyncio import Redis
from src.infrastructure.config.settings import get_settings


settings = get_settings()

def create_redis_client(redis_url: str = settings.REDIS_URL.get_secret_value()) -> Redis:
    return Redis.from_url(
        redis_url, 
        decode_responses=True,
        socket_timeout=30,
        socket_connect_timeout=30,
    )

