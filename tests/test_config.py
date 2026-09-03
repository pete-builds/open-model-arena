"""Tests for config loading and model lookups."""

import pytest

from app.config import load_config
from app.errors import ConfigError


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


def test_load_config_file_not_found():
    with pytest.raises(ConfigError, match="config file not found"):
        load_config("/nonexistent/path.yaml")


def test_load_config_invalid_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{{not valid yaml")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(str(bad))


def test_load_config_missing_sections(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("foo: bar\n")
    with pytest.raises(ConfigError, match="must contain"):
        load_config(str(empty))


def test_load_config_unknown_provider(tmp_path):
    cfg = tmp_path / "bad_ref.yaml"
    cfg.write_text("""
providers:
  gw:
    base_url: http://test/v1
    api_key: k
models:
  - id: m1
    provider: nonexistent
    display_name: M1
    model_id: m1
""")
    with pytest.raises(ConfigError, match="unknown provider"):
        load_config(str(cfg))


def test_load_config_valid(tmp_path):
    cfg = tmp_path / "good.yaml"
    cfg.write_text("""
providers:
  gw:
    base_url: http://test/v1
    api_key: testkey
models:
  - id: m1
    provider: gw
    display_name: Model One
    model_id: m1-v1
""")
    config = load_config(str(cfg))
    assert len(config.models) == 1
    assert config.get_model("m1").display_name == "Model One"


# --- reasoning flag ---


def _write_models(tmp_path, reasoning_line: str) -> str:
    path = tmp_path / "models.yaml"
    path.write_text(
        "providers:\n"
        "  p:\n"
        "    base_url: http://x/v1\n"
        "    api_key: k\n"
        "models:\n"
        "  - id: m\n"
        "    provider: p\n"
        "    display_name: M\n"
        "    model_id: m\n"
        "    categories: [general]\n"
        f"{reasoning_line}"
        "  - id: n\n"
        "    provider: p\n"
        "    display_name: N\n"
        "    model_id: n\n"
        "    categories: [general]\n"
    )
    return str(path)


def test_reasoning_defaults_to_auto(tmp_path):
    cfg = load_config(_write_models(tmp_path, ""))
    assert cfg.get_model("m").reasoning is None


def test_reasoning_true_and_false(tmp_path):
    cfg = load_config(_write_models(tmp_path, "    reasoning: true\n"))
    assert cfg.get_model("m").reasoning is True
    cfg = load_config(_write_models(tmp_path, "    reasoning: false\n"))
    assert cfg.get_model("m").reasoning is False
    cfg = load_config(_write_models(tmp_path, "    reasoning: auto\n"))
    assert cfg.get_model("m").reasoning is None


def test_reasoning_rejects_garbage(tmp_path):
    with pytest.raises(ConfigError, match="reasoning"):
        load_config(_write_models(tmp_path, "    reasoning: sometimes\n"))
