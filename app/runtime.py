"""Process-wide singletons shared by the route modules.

Kept out of ``main.py`` so routers can import them without a circular import.
Tests monkeypatch attributes here (``runtime.suites``, ``runtime.config``).
"""

from __future__ import annotations

from .config import load_config
from .ratelimit import RateLimiter
from .store import Store
from .suites import load_suites

config = load_config()
suites = load_suites()
store = Store()

# Battle creation is the expensive call (two paid model requests), so it gets
# the tight limiter. Audience votes cost nothing upstream, but a whole class
# usually arrives from one NAT address (or one Funnel proxy), so that limiter
# is per-IP generous and the real cap is per-poll voters in the store.
battle_limiter = RateLimiter(max_requests=10, window_seconds=60)
audience_limiter = RateLimiter(max_requests=300, window_seconds=60)
