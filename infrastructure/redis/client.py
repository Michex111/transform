from redis.asyncio import Redis


def create_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url, 
        decode_responses=True,
        socket_timeout=30,
        socket_connect_timeout=30,
    )

