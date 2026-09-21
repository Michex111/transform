from redis.asyncio import Redis
from src.infrastructure.config.settings import get_settings

# Kept short on purpose. A dead or firewalled Redis does not refuse the
# connection, it swallows the SYN, so connect blocks until the OS gives up.
# With the previous 30s value every caller stalled for the full timeout on its
# first call — ``build_rate_limit_middleware`` documents a "short connect
# timeout so an unreachable Redis degrades to the in-memory fallback instead of
# stalling requests", and integration tests paid ~30s per app instance.
#
# ``socket_timeout`` stays generous: blocking reads (e.g. the worker's
# XREADGROUP) legitimately hold a connection open for a long time.
_REDIS_CONNECT_TIMEOUT = 2


def create_redis_client(redis_url: str | None = None) -> Redis:
    # Resolve lazily: a default argument would evaluate ``get_settings()`` at
    # import time, so importing this module could fail (or freeze) before the
    # application is configured.
    if redis_url is None:
        redis_url = get_settings().REDIS_URL.get_secret_value()
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=30,
        socket_connect_timeout=_REDIS_CONNECT_TIMEOUT,
    )

