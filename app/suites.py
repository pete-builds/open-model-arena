"""Eval-suite loader + runner.

Suites are YAML files under ``suites/``. Each is a named prompt set. Running
a suite fires one battle per prompt, uses the configured judge to cast the
vote automatically, and records aggregate results (win/loss/tie per model)
under a ``run_id`` so a team can diff runs over time.

Suites are read once on server start. Adding a new file requires a restart —
suites are meant to be small, versioned artifacts under source control, not
live-editable in production.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import ConfigError

log = logging.getLogger("arena.suites")

SUITES_DIR = "suites"


@dataclass
class SuitePrompt:
    id: str
    prompt: str


@dataclass
class Suite:
    name: str
    description: str
    category: str
    prompts: list[SuitePrompt] = field(default_factory=list)


def _load_one(path: Path) -> Suite | None:
    if path.name.endswith(".yaml.example") or path.name == "README.md":
        return None
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in suite {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"suite {path} must be a YAML mapping")
    name = raw.get("name")
    if not name:
        raise ConfigError(f"suite {path} missing 'name'")
    prompts_raw = raw.get("prompts") or []
    if not isinstance(prompts_raw, list) or not prompts_raw:
        raise ConfigError(f"suite {name} must have at least one prompt")
    seen_ids: set[str] = set()
    prompts: list[SuitePrompt] = []
    for p in prompts_raw:
        pid = p.get("id")
        text = p.get("prompt")
        if not pid or not text:
            raise ConfigError(f"suite {name}: every prompt needs 'id' and 'prompt'")
        if pid in seen_ids:
            raise ConfigError(f"suite {name}: duplicate prompt id {pid!r}")
        seen_ids.add(pid)
        prompts.append(SuitePrompt(id=str(pid), prompt=str(text)))
    return Suite(
        name=str(name),
        description=str(raw.get("description") or ""),
        category=str(raw.get("category") or "general"),
        prompts=prompts,
    )


def load_suites(directory: str = SUITES_DIR) -> dict[str, Suite]:
    """Load every ``*.yaml`` (not ``.yaml.example``) under ``directory``.

    Missing directory is not an error — it means the operator hasn't defined
    any suites yet, and ``/api/suites`` returns an empty list.
    """
    path = Path(directory)
    if not path.is_dir():
        log.info("no suites directory at %s; skipping", directory)
        return {}
    suites: dict[str, Suite] = {}
    for f in sorted(path.iterdir()):
        if f.suffix != ".yaml":
            continue
        loaded = _load_one(f)
        if loaded is None:
            continue
        if loaded.name in suites:
            raise ConfigError(f"duplicate suite name '{loaded.name}' in {f}")
        suites[loaded.name] = loaded
    log.info("loaded %d suites from %s", len(suites), directory)
    return suites
