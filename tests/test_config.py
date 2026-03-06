"""Tests for config loading and model lookups."""

from app.config import Config, Provider, Model


def test_get_model(test_config):
    model = test_config.get_model("model-alpha")
    assert model is not None
    assert model.display_name == "Alpha Model"


def test_get_model_not_found(test_config):
    assert test_config.get_model("nonexistent") is None


def test_enabled_models_excludes_disabled(test_config):
    enabled = test_config.enabled_models()
    ids = [m.id for m in enabled]
    assert "model-disabled" not in ids
    assert "model-alpha" in ids


def test_enabled_models_by_category(test_config):
    coding = test_config.enabled_models("coding")
    ids = [m.id for m in coding]
    assert "model-alpha" in ids
    assert "model-beta" in ids
    assert "model-local" not in ids  # local only has "general"


def test_get_provider(test_config):
    provider = test_config.get_provider("test-gateway")
    assert provider.base_url == "http://fake:8080/v1"
    assert provider.request_surcharge == 0.002
