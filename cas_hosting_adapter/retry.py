"""Retry only safe, idempotent provider operations."""
from __future__ import annotations

import random
import time
from collections.abc import Callable


def is_retryable(error: BaseException) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    return (
        isinstance(error, OSError)
        or status in {408, 429}
        or isinstance(status, int) and 500 <= status <= 599
    )


def retry_safe[**P, T](
    operation: Callable[P, T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
) -> Callable[P, T]:
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in range(3):
            try:
                return operation(*args, **kwargs)
            except BaseException as error:
                if attempt == 2 or not is_retryable(error):
                    raise
                sleep(random_value() * min(5.0, 0.5 * (2**attempt)))
        raise AssertionError("unreachable")
    return wrapped
