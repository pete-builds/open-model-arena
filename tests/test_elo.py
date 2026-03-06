"""Unit tests for ELO rating math."""

from app.store import _update_elo


def test_equal_ratings_winner_a():
    new_a, new_b = _update_elo(1500.0, 1500.0, "a")
    assert new_a == 1516.0
    assert new_b == 1484.0


def test_equal_ratings_winner_b():
    new_a, new_b = _update_elo(1500.0, 1500.0, "b")
    assert new_a == 1484.0
    assert new_b == 1516.0


def test_equal_ratings_tie():
    new_a, new_b = _update_elo(1500.0, 1500.0, "tie")
    assert new_a == 1500.0
    assert new_b == 1500.0


def test_underdog_wins_gains_more():
    """Lower-rated player gains more points for an upset."""
    new_a, new_b = _update_elo(1200.0, 1800.0, "a")
    gain_a = new_a - 1200.0
    # Underdog should gain close to 32 (max K)
    assert gain_a > 28
    assert gain_a <= 32


def test_favorite_wins_gains_less():
    """Higher-rated player gains fewer points for expected win."""
    new_a, new_b = _update_elo(1800.0, 1200.0, "a")
    gain_a = new_a - 1800.0
    # Favorite should gain very little
    assert gain_a < 4
    assert gain_a > 0


def test_elo_is_zero_sum():
    """Total rating change should be zero (what one gains, other loses)."""
    for winner in ("a", "b", "tie"):
        new_a, new_b = _update_elo(1600.0, 1400.0, winner)
        total_before = 1600.0 + 1400.0
        total_after = new_a + new_b
        assert abs(total_after - total_before) < 0.001


def test_ratings_never_change_by_more_than_k():
    new_a, new_b = _update_elo(1500.0, 1500.0, "a")
    assert abs(new_a - 1500.0) <= 32
    assert abs(new_b - 1500.0) <= 32
