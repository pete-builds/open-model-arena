"""Tests for the eval-suite loader."""

from __future__ import annotations

import pytest

from app.errors import ConfigError
from app.suites import Suite, SuitePrompt, load_suites


def _write(dir_path, name: str, body: str) -> None:
    (dir_path / name).write_text(body)


def test_load_suites_missing_dir_is_empty(tmp_path):
    assert load_suites(str(tmp_path / "does-not-exist")) == {}


def test_load_suites_skips_example_and_readme(tmp_path):
    _write(tmp_path, "example.yaml.example", "name: skip\nprompts: [{id: p, prompt: q}]")
    _write(tmp_path, "README.md", "docs")
    assert load_suites(str(tmp_path)) == {}


def test_load_one_suite(tmp_path):
    _write(
        tmp_path,
        "myset.yaml",
        "name: myset\ncategory: coding\nprompts:\n  - id: p1\n    prompt: hello\n",
    )
    result = load_suites(str(tmp_path))
    assert set(result) == {"myset"}
    suite = result["myset"]
    assert isinstance(suite, Suite)
    assert suite.name == "myset"
    assert suite.category == "coding"
    assert suite.prompts == [SuitePrompt(id="p1", prompt="hello")]


def test_load_multiple_suites_sorted(tmp_path):
    _write(tmp_path, "b.yaml", "name: b\nprompts: [{id: x, prompt: y}]")
    _write(tmp_path, "a.yaml", "name: a\nprompts: [{id: x, prompt: y}]")
    result = load_suites(str(tmp_path))
    assert list(result) == ["a", "b"]


def test_load_rejects_missing_name(tmp_path):
    _write(tmp_path, "bad.yaml", "prompts: [{id: p, prompt: q}]")
    with pytest.raises(ConfigError, match="missing 'name'"):
        load_suites(str(tmp_path))


def test_load_rejects_no_prompts(tmp_path):
    _write(tmp_path, "empty.yaml", "name: empty\nprompts: []")
    with pytest.raises(ConfigError, match="at least one prompt"):
        load_suites(str(tmp_path))


def test_load_rejects_duplicate_prompt_ids(tmp_path):
    _write(
        tmp_path,
        "dup.yaml",
        "name: dup\nprompts:\n  - id: p\n    prompt: a\n  - id: p\n    prompt: b\n",
    )
    with pytest.raises(ConfigError, match="duplicate prompt id"):
        load_suites(str(tmp_path))


def test_load_rejects_prompt_missing_fields(tmp_path):
    _write(
        tmp_path,
        "bad.yaml",
        "name: bad\nprompts:\n  - id: p1\n",
    )
    with pytest.raises(ConfigError, match="'id' and 'prompt'"):
        load_suites(str(tmp_path))


def test_load_rejects_duplicate_suite_names(tmp_path):
    _write(tmp_path, "a.yaml", "name: same\nprompts: [{id: p, prompt: q}]")
    _write(tmp_path, "b.yaml", "name: same\nprompts: [{id: p, prompt: q}]")
    with pytest.raises(ConfigError, match="duplicate suite name"):
        load_suites(str(tmp_path))


def test_load_rejects_bad_yaml(tmp_path):
    _write(tmp_path, "broken.yaml", "name: [unbalanced\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_suites(str(tmp_path))
