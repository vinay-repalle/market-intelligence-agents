"""Retry with exponential backoff for transient errors only."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from .errors import TransientError

log = logging.getLogger(__name__)
T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    *,
    what: str,
    attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call ``fn``, retrying only on TransientError. Permanent errors propagate at once."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except TransientError as exc:
            if attempt == attempts:
                log.error("%s failed after %d attempts: %s", what, attempts, exc)
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1))
            delay += random.uniform(0, delay * 0.1)  # noqa: S311 - jitter, not security
            if exc.retry_after is not None:
                delay = min(max_delay, max(delay, exc.retry_after))
            log.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                what, attempt, attempts, exc, delay,
            )
            (sleep or time.sleep)(delay)
    raise AssertionError("unreachable")  # pragma: no cover
