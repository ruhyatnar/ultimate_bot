import asyncio
import logging
import random
from functools import wraps

logger = logging.getLogger(__name__)


class NonRetryableError(Exception):
    """Raise for permanent failures (bad config, invalid credentials, rejected
    parameters) — async_retry re-raises these immediately instead of burning
    retries/backoff on an error that can never succeed."""


def async_retry(max_retries=5, backoff=2, transient_exceptions=(Exception,), permanent_exceptions=(NonRetryableError,)):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except permanent_exceptions:
                    raise
                except transient_exceptions as e:
                    retries += 1
                    if retries > max_retries:
                        logger.error(f"Max retries exceeded for {func.__name__}: {e}")
                        raise
                    wait = backoff ** retries * (0.5 + random.random() * 0.5)
                    logger.warning(f"Retry {retries}/{max_retries} for {func.__name__} in {wait:.2f}s: {e}")
                    await asyncio.sleep(wait)
        return wrapper
    return decorator
