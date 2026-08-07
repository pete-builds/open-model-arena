from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import yaml

from .errors import ConfigError

log = logging.getLogger("arena.config")


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    request_surcharge: float = 0.0
    timeout: int = 30
    local: bool = False


@dataclass
class Model:
    id: str
    provider_name: str
    display_name: str
    model_id: str
    input_cost_per_1m: float
    output_cost_per_1m: float
    categories: list[str] = field(default_factory=list)
    enabled: bool = True


DEFAULT_JUDGE_RUBRIC = """You are an impartial judge comparing two AI responses to the same prompt.
Score each on: correctness, faithfulness to the prompt, helpfulness, and clarity.
Penalize hallucinations and evasive answers.
Return ONLY a compact JSON object of the shape:
  {"winner": "a" | "b" | "tie", "reasoning": "<one or two sentences>"}
No prose outside the JSON. If either response is empty, errored, or unusable, the other wins."""


@dataclass
class Judge:
    """Optional judge configuration for LLM-as-judge automated voting."""

    model_id: str
    rubric: str = DEFAULT_JUDGE_RUBRIC


@dataclass
class Config:
    providers: dict[str, Provider]
    models: list[Model]
    judge: Judge | None = None

    def get_provider(self, name: str) -> Provider:
        return self.providers[name]

    def get_model(self, model_id: str) -> Model | None:
        for m in self.models:
            if m.id == model_id:
                return m
        return None

    def enabled_models(self, category: str | None = None) -> list[Model]:
        result = [m for m in self.models if m.enabled]
        if category:
            result = [m for m in result if category in m.categories]
        return result

    def known_categories(self) -> set[str]:
        """Every category that appears on at least one enabled model.

        Used to reject arbitrary caller-supplied category strings at battle
        creation. 'overall' is intentionally excluded — that's the aggregate
        ratings bucket, not a battle category.
        """
        return {cat for m in self.models if m.enabled for cat in m.categories}

    def judge_model(self) -> Model | None:
        """Return the configured judge model, or None if judge is disabled."""
        if not self.judge:
            return None
        return self.get_model(self.judge.model_id)


def load_config(path: str = "models.yaml") -> Config:
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}")

    if not raw or "providers" not in raw or "models" not in raw:
        raise ConfigError(f"config file {path} must contain 'providers' and 'models' sections")

    providers = {}
    for name, prov in raw["providers"].items():
        api_key = prov.get("api_key", "")
        if "api_key_env" in prov:
            api_key = os.environ.get(prov["api_key_env"], "")
        providers[name] = Provider(
            name=name,
            base_url=prov["base_url"],
            api_key=api_key,
            request_surcharge=prov.get("request_surcharge", 0.0),
            timeout=prov.get("timeout", 30),
            local=prov.get("local", False),
        )

    models = []
    for m in raw["models"]:
        if m.get("provider") not in providers:
            raise ConfigError(f"model '{m.get('id')}' references unknown provider '{m.get('provider')}'")
        models.append(
            Model(
                id=m["id"],
                provider_name=m["provider"],
                display_name=m["display_name"],
                model_id=m["model_id"],
                input_cost_per_1m=m.get("input_cost_per_1m", 0.0),
                output_cost_per_1m=m.get("output_cost_per_1m", 0.0),
                categories=m.get("categories", []),
                enabled=m.get("enabled", True),
            )
        )

    judge: Judge | None = None
    judge_raw = raw.get("judge")
    if judge_raw:
        judge_model_id = judge_raw.get("model")
        if not judge_model_id:
            raise ConfigError("judge: entry must specify 'model'")
        if not any(m.id == judge_model_id for m in models):
            raise ConfigError(f"judge references unknown model id '{judge_model_id}'")
        judge = Judge(
            model_id=judge_model_id,
            rubric=judge_raw.get("rubric") or DEFAULT_JUDGE_RUBRIC,
        )
        log.info("judge configured: %s", judge_model_id)

    log.info(
        "loaded %d providers, %d models (%d enabled)", len(providers), len(models), sum(1 for m in models if m.enabled)
    )
    return Config(providers=providers, models=models, judge=judge)
