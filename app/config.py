from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


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


@dataclass
class Config:
    providers: dict[str, Provider]
    models: list[Model]

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


def load_config(path: str = "models.yaml") -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

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
        models.append(Model(
            id=m["id"],
            provider_name=m["provider"],
            display_name=m["display_name"],
            model_id=m["model_id"],
            input_cost_per_1m=m.get("input_cost_per_1m", 0.0),
            output_cost_per_1m=m.get("output_cost_per_1m", 0.0),
            categories=m.get("categories", []),
            enabled=m.get("enabled", True),
        ))

    return Config(providers=providers, models=models)
