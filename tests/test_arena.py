"""Tests for model selection logic and battle streaming."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.arena import estimate_cost, get_client, select_models, stream_battle
from app.config import Config, Model, Provider


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
    with pytest.raises(ValueError, match="need at least 2"):
        select_models(test_config, "nonexistent-category")


def test_select_models_all_local_fallback():
    """When all models are local, select_models still picks two different ones."""
    providers = {
        "local1": Provider(name="local1", base_url="http://l1/v1", api_key="", local=True),
        "local2": Provider(name="local2", base_url="http://l2/v1", api_key="", local=True),
    }
    models = [
        Model(
            id="a",
            provider_name="local1",
            display_name="A",
            model_id="a",
            input_cost_per_1m=0,
            output_cost_per_1m=0,
            categories=["general"],
            enabled=True,
        ),
        Model(
            id="b",
            provider_name="local2",
            display_name="B",
            model_id="b",
            input_cost_per_1m=0,
            output_cost_per_1m=0,
            categories=["general"],
            enabled=True,
        ),
    ]
    cfg = Config(providers=providers, models=models)
    for _ in range(10):
        a, b = select_models(cfg, "general")
        assert a.id != b.id


def test_select_models_one_gateway_one_local():
    """With exactly one gateway and one local model, both get selected."""
    providers = {
        "gw": Provider(name="gw", base_url="http://gw/v1", api_key="k"),
        "loc": Provider(name="loc", base_url="http://loc/v1", api_key="", local=True),
    }
    models = [
        Model(
            id="gw-m",
            provider_name="gw",
            display_name="GW",
            model_id="gw",
            input_cost_per_1m=1,
            output_cost_per_1m=5,
            categories=["general"],
            enabled=True,
        ),
        Model(
            id="loc-m",
            provider_name="loc",
            display_name="Loc",
            model_id="loc",
            input_cost_per_1m=0,
            output_cost_per_1m=0,
            categories=["general"],
            enabled=True,
        ),
    ]
    cfg = Config(providers=providers, models=models)
    for _ in range(10):
        a, b = select_models(cfg, "general")
        assert {a.id, b.id} == {"gw-m", "loc-m"}


def test_estimate_cost_with_surcharge(test_config):
    model = test_config.get_model("model-alpha")
    # input: (1000/1M) * 3.0 = 0.003
    # output: (500/1M) * 15.0 = 0.0075
    # surcharge: 0.002
    # total: 0.0125
    cost = estimate_cost(model, test_config, 1000, 500)
    assert abs(cost - 0.0125) < 0.0001


def test_estimate_cost_free_model(test_config):
    model = test_config.get_model("model-local")
    cost = estimate_cost(model, test_config, 1000, 500)
    assert cost == 0.0


def test_estimate_cost_zero_tokens(test_config):
    model = test_config.get_model("model-alpha")
    cost = estimate_cost(model, test_config, 0, 0)
    assert cost == 0.002  # just the surcharge


def test_get_client(test_config):
    model = test_config.get_model("model-alpha")
    client = get_client(test_config, model)
    assert client.base_url == "http://fake:8080/v1/"
    assert client.api_key == "test-key"


def test_get_client_empty_api_key(test_config):
    model = test_config.get_model("model-local")
    client = get_client(test_config, model)
    assert client.api_key == "none"


def _make_mock_chunk(content=None, usage=None):
    """Build a mock chat completion chunk."""
    chunk = MagicMock()
    if content:
        choice = MagicMock()
        choice.delta.content = content
        chunk.choices = [choice]
    else:
        chunk.choices = []
    chunk.usage = usage
    return chunk


async def _collect_events(async_gen):
    """Collect all SSE events from an async generator."""
    events = []
    async for event in async_gen:
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_stream_battle_success(test_config, test_store):
    """Full happy-path stream battle with mocked OpenAI responses."""
    battle_id = await test_store.create_battle("Hello", "general", "model-alpha", "model-beta")

    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5

    chunks = [
        _make_mock_chunk(content="Hello "),
        _make_mock_chunk(content="world"),
        _make_mock_chunk(usage=usage),
    ]

    async def mock_create(**kwargs):
        mock_stream = AsyncMock()
        mock_stream.__aiter__ = lambda self: self
        mock_stream._items = list(chunks)

        async def anext_impl(self):
            if self._items:
                return self._items.pop(0)
            raise StopAsyncIteration

        mock_stream.__anext__ = anext_impl
        return mock_stream

    mock_client = AsyncMock()
    mock_client.chat.completions.create = mock_create

    with patch("app.arena.get_client", return_value=mock_client):
        events = await _collect_events(stream_battle(test_config, test_store, battle_id))

    event_text = "".join(events)
    assert "model_a" in event_text or "model_b" in event_text
    assert "battle_complete" in event_text


@pytest.mark.asyncio
async def test_stream_battle_not_found(test_config, test_store):
    """Stream for a non-existent battle yields an error event."""
    events = await _collect_events(stream_battle(test_config, test_store, "nonexistent12345"))
    assert len(events) == 1
    assert "battle not found" in events[0]


@pytest.mark.asyncio
async def test_stream_battle_model_not_in_config(test_store):
    """Stream with a model ID not in config yields an error event."""
    battle_id = await test_store.create_battle("Hi", "general", "no-such-model", "also-missing")

    empty_config = Config(providers={}, models=[])
    events = await _collect_events(stream_battle(empty_config, test_store, battle_id))
    assert len(events) == 1
    assert "model not found" in events[0]


@pytest.mark.asyncio
async def test_stream_battle_timeout(test_config, test_store):
    """When a model call times out, an error event is emitted."""
    battle_id = await test_store.create_battle("Hello", "general", "model-alpha", "model-beta")

    async def mock_create_timeout(**kwargs):
        await asyncio.sleep(999)

    mock_client = AsyncMock()
    mock_client.chat.completions.create = mock_create_timeout

    # Set a very short timeout
    test_config.providers["test-gateway"].timeout = 0.1

    with patch("app.arena.get_client", return_value=mock_client):
        events = await _collect_events(stream_battle(test_config, test_store, battle_id))

    event_text = "".join(events)
    assert "timed out" in event_text


@pytest.mark.asyncio
async def test_stream_battle_api_error(test_config, test_store):
    """When the API raises an exception, error is sanitized."""
    battle_id = await test_store.create_battle("Hello", "general", "model-alpha", "model-beta")

    async def mock_create_error(**kwargs):
        raise ConnectionError("secret-internal-url.example.com refused")

    mock_client = AsyncMock()
    mock_client.chat.completions.create = mock_create_error

    with patch("app.arena.get_client", return_value=mock_client):
        events = await _collect_events(stream_battle(test_config, test_store, battle_id))

    event_text = "".join(events)
    # Should NOT contain the raw error message
    assert "secret-internal-url" not in event_text
    # Should contain the sanitized message
    assert "model call failed" in event_text
