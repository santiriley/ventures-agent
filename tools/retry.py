"""
tools/retry.py — Exponential backoff decorator for flaky network calls.

Usage:
    from tools.retry import with_retry

    @with_retry(max_attempts=3, base_delay=2.0)
    def call_api():
        ...
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)

# Exceptions that should never be retried (auth errors, schema mismatches)
_NO_RETRY = (EnvironmentError, ValueError, KeyboardInterrupt, SystemExit)


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable[[F], F]:
    """
    Decorator that retries a function up to max_attempts times using
    exponential backoff.

    Args:
        max_attempts: Total number of attempts (including the first).
        base_delay:   Initial wait in seconds before the first retry.
        backoff:      Multiplier applied to delay after each failure.
        exceptions:   Tuple of exception types to catch and retry on.
                      EnvironmentError and ValueError are never retried.
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except _NO_RETRY:
                    # Auth / schema errors — fail immediately, no retry
                    raise
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    logger.warning(
                        f"[retry] {func.__name__} attempt {attempt}/{max_attempts} "
                        f"failed: {exc}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= backoff
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator
