"""Login throttling.

A fixed-window counter held in process memory. That is enough for the MVP, which runs a
single API container, and it is deliberately simple: the failure mode is that a burst
spread across replicas is under-counted, never that a legitimate user is locked out
permanently. A production deployment with several API replicas needs a shared counter
(PostgreSQL or the reverse proxy), which is recorded as a follow-up in docs/decisions.md.
"""

import threading
import time
from collections import defaultdict, deque

LOGIN_ATTEMPT_LIMIT = 10
LOGIN_WINDOW_SECONDS = 300.0


class RateLimitExceeded(Exception):
    """Too many attempts for this key within the window."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"Too many attempts; retry in {retry_after_seconds} seconds")
        self.retry_after_seconds = retry_after_seconds


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Record an attempt, raising RateLimitExceeded once the key is over its limit."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()

            if len(hits) >= self.limit:
                retry_after = self.window_seconds - (now - hits[0])
                raise RateLimitExceeded(max(int(retry_after), 1))

            hits.append(now)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


login_limiter = RateLimiter(LOGIN_ATTEMPT_LIMIT, LOGIN_WINDOW_SECONDS)
