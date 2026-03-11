import time
from collections import defaultdict


class RateLimiter:
    """Simple in-memory sliding window rate limiter."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300  # purge stale keys every 5 minutes

    def _maybe_cleanup(self, now: float) -> None:
        """Remove keys with no recent requests to prevent unbounded memory growth."""
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        window_start = now - self.window_seconds
        stale = [k for k, ts in self.requests.items() if not ts or ts[-1] <= window_start]
        for k in stale:
            del self.requests[k]

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        self._maybe_cleanup(now)
        window_start = now - self.window_seconds

        # Drop expired entries
        self.requests[key] = [t for t in self.requests[key] if t > window_start]

        if len(self.requests[key]) >= self.max_requests:
            return False

        self.requests[key].append(now)
        return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        window_start = now - self.window_seconds
        active = [t for t in self.requests[key] if t > window_start]
        return max(0, self.max_requests - len(active))
