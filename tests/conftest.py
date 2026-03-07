"""Shared fixtures for Open Model Arena tests."""

import os
import tempfile

# Set required env vars BEFORE any app imports
os.environ.setdefault("ARENA_PASSPHRASE", "test-passphrase")
os.environ.setdefault("AUTH_TOKEN_SECRET", "test-secret-key")

import pytest
import pytest_asyncio

from app.config import Config, Provider, Model
from app.store import Store


@pytest.fixture
def test_config():
    """Minimal config with two fake models for testing."""
    providers = {
        "test-gateway": Provider(
            name="test-gateway",
            base_url="http://fake:8080/v1",
            api_key="test-key",
            request_surcharge=0.002,
            timeout=30,
        ),
        "test-local": Provider(
            name="test-local",
            base_url="http://fake:11434/v1",
            api_key="",
            request_surcharge=0.0,
            timeout=60,
            local=True,
        ),
    }
    models = [
        Model(
            id="model-alpha",
            provider_name="test-gateway",
            display_name="Alpha Model",
            model_id="alpha-v1",
            input_cost_per_1m=3.0,
            output_cost_per_1m=15.0,
            categories=["general", "coding"],
            enabled=True,
        ),
        Model(
            id="model-beta",
            provider_name="test-gateway",
            display_name="Beta Model",
            model_id="beta-v1",
            input_cost_per_1m=5.0,
            output_cost_per_1m=25.0,
            categories=["general", "coding"],
            enabled=True,
        ),
        Model(
            id="model-local",
            provider_name="test-local",
            display_name="Local Model",
            model_id="local-7b",
            input_cost_per_1m=0.0,
            output_cost_per_1m=0.0,
            categories=["general"],
            enabled=True,
        ),
        Model(
            id="model-disabled",
            provider_name="test-gateway",
            display_name="Disabled Model",
            model_id="disabled-v1",
            input_cost_per_1m=1.0,
            output_cost_per_1m=5.0,
            categories=["general"],
            enabled=False,
        ),
    ]
    return Config(providers=providers, models=models)


@pytest_asyncio.fixture
async def test_store(tmp_path):
    """In-memory-like store using a temp file DB."""
    db_path = str(tmp_path / "test_arena.db")
    s = Store(db_path=db_path)
    await s.connect()
    yield s
    await s.close()
