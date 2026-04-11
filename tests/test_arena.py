"""Tests for model selection logic."""

from app.arena import estimate_cost, select_models


def test_select_models_returns_two_different(test_config):
    for _ in range(20):  # Run multiple times since it's random
        a, b = select_models(test_config, "general")
        assert a.id != b.id


def test_select_models_only_enabled(test_config):
    for _ in range(20):
        a, b = select_models(test_config, "general")
        assert a.enabled
        assert b.enabled


def test_select_models_respects_category(test_config):
    for _ in range(20):
        a, b = select_models(test_config, "coding")
        assert "coding" in a.categories
        assert "coding" in b.categories


def test_select_models_too_few_raises(test_config):
    import pytest
    with pytest.raises(ValueError, match="need at least 2"):
        select_models(test_config, "nonexistent-category")


def test_estimate_cost_with_surcharge(test_config):
    model = test_config.get_model("model-alpha")
    # 1000 input tokens, 500 output tokens
    # input: (1000/1M) * 3.0 = 0.003
    # output: (500/1M) * 15.0 = 0.0075
    # surcharge: 0.002
    # total: 0.0125
    cost = estimate_cost(model, test_config, 1000, 500)
    assert abs(cost - 0.0125) < 0.0001


def test_estimate_cost_free_model(test_config):
    model = test_config.get_model("model-local")
    cost = estimate_cost(model, test_config, 1000, 500)
    assert cost == 0.0  # No cost, no surcharge
