"""Tests for the rate limiter."""

import time
from unittest.mock import patch

from app.ratelimit import RateLimiter


def test_allows_under_limit():
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    assert limiter.is_allowed("user1") is True
    assert limiter.is_allowed("user1") is True
    assert limiter.is_allowed("user1") is True


def test_blocks_over_limit():
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    assert limiter.is_allowed("user1") is True
    assert limiter.is_allowed("user1") is True
    assert limiter.is_allowed("user1") is False


def test_separate_keys():
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    assert limiter.is_allowed("user1") is True
    assert limiter.is_allowed("user2") is True
    assert limiter.is_allowed("user1") is False


def test_window_expiry():
    limiter = RateLimiter(max_requests=1, window_seconds=10)
    assert limiter.is_allowed("user1") is True
    assert limiter.is_allowed("user1") is False

    # Simulate time passing beyond the window
    with patch("time.monotonic", return_value=time.monotonic() + 11):
        assert limiter.is_allowed("user1") is True


def test_remaining():
    limiter = RateLimiter(max_requests=5, window_seconds=60)
    assert limiter.remaining("user1") == 5
    limiter.is_allowed("user1")
    limiter.is_allowed("user1")
    assert limiter.remaining("user1") == 3
