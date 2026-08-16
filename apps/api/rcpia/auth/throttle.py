"""Login throttling.

Two independent budgets, both counting **failures only**, so a busy legitimate user is
never locked out by their own successful logins:

- per account, which stops guessing at one address;
- per client address, which stops one host spraying many addresses.

Memory is bounded. Keys expire with their window and the table is capped, evicting the
least recently touched key first, so an attacker cycling random addresses cannot grow it
without limit.

State lives in this process. With one API container that is exact; running several
replicas under-counts by the number of replicas, which is recorded in docs/decisions.md
as a prerequisite for scaling the API out.
"""

import threading
import time
from collections import OrderedDict, deque

ACCOUNT_ATTEMPT_LIMIT = 10
CLIENT_ATTEMPT_LIMIT = 30
WINDOW_SECONDS = 300.0
MAX_TRACKED_KEYS = 10_000


class RateLimitExceeded(Exception):
    """Too many recent failures for this key."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"Too many attempts; retry in {retry_after_seconds} seconds")
        self.retry_after_seconds = retry_after_seconds


class RateLimiter:
    def __init__(
        self, limit: int, window_seconds: float = WINDOW_SECONDS, max_keys: int = MAX_TRACKED_KEYS
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def ensure_allowed(self, key: str) -> None:
        """Raise if this key is over its failure budget. Records nothing."""
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            hits = self._failures.get(key)
            if hits is None:
                return
            self._failures.move_to_end(key)
            if len(hits) >= self.limit:
                raise RateLimitExceeded(max(int(self.window_seconds - (now - hits[0])), 1))

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            hits = self._failures.setdefault(key, deque())
            self._failures.move_to_end(key)
            hits.append(now)
            self._evict_if_oversized()

    def reset(self, key: str | None = None) -> None:
        """Forget a key's failures — called after a successful login."""
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)

    def _expire(self, now: float) -> None:
        """Drop stale hits, and keys left with none. Called under the lock."""
        for key in list(self._failures):
            hits = self._failures[key]
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if not hits:
                del self._failures[key]

    def _evict_if_oversized(self) -> None:
        while len(self._failures) > self.max_keys:
            self._failures.popitem(last=False)


account_limiter = RateLimiter(ACCOUNT_ATTEMPT_LIMIT)
client_limiter = RateLimiter(CLIENT_ATTEMPT_LIMIT)


def reset_all() -> None:
    account_limiter.reset()
    client_limiter.reset()
