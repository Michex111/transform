from functools import wraps
import random
import asyncio

def retry_on_exception(max_retries=3, exceptions=(Exception,), base_delay=1, max_delay=60, logger=None):
    """
    Decorator to retry a function call on specified exceptions.

    :param max_retries: Maximum number of retries before giving up.
    :param exceptions: A tuple of exception classes to catch and retry on.
    :param base_delay: Initial delay in seconds between retries.
    :param max_delay: Maximum delay in seconds between retries.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retries = max(1, int(max_retries))
            attempts = 1
            while True:
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    # Cancellation should not be retried.
                    raise
                except exceptions as e:
                    if attempts >= retries:
                        if logger:
                            logger.error(f"Function {func.__name__} failed after {attempts} attempts. Raising exception.")
                        raise
                    else:
                        delay = min(base_delay * (2 ** (attempts - 1)), max_delay)
                        jitter = random.uniform(0, delay)  # Add jitter to avoid thundering herd

                        if logger:
                            logger.warning(f"Function {func.__name__} failed with {e}. Retrying {attempts}/{retries} after {jitter:.2f} seconds...")

                        await asyncio.sleep(jitter)
                        attempts += 1
        return wrapper
    return decorator
