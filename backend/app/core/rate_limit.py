import threading
import time
from collections import defaultdict, deque

from app.core.errors import RateLimited


class SlidingWindowLimiter:
    """In-process sliding-window limiter.

    Good enough for a single instance; with multiple replicas this should move to
    Redis (documented trade-off in ARCHITECTURE.md).
    """

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                retry = int(self.window - (now - q[0])) + 1
                raise RateLimited(
                    "Too many attempts, please retry later",
                    details={"retry_after_seconds": retry},
                )
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
