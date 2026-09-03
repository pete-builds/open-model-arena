"""Tests for model selection logic and battle streaming."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import BadRequestError

from app.arena import (
    completion_kwargs,
    estimate_cost,
    get_client,
    normalize_reasoning_effort,
    pick_opponent,
    select_models,
    stream_battle,
)
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


def _mock_stream_client(chunks, call_counter):
    """Client whose create() increments a counter each call and yields chunks."""

    async def mock_create(**kwargs):
        call_counter["n"] += 1
        mock_stream = AsyncMock()
        mock_stream.__aiter__ = lambda self: self
        mock_stream._items = list(chunks)

        async def anext_impl(self):
            if self._items:
                return self._items.pop(0)
            raise StopAsyncIteration

        mock_stream.__anext__ = anext_impl
        return mock_stream

    client = AsyncMock()
    client.chat.completions.create = mock_create
    return client


@pytest.mark.asyncio
async def test_stream_battle_replays_on_second_call(test_config, test_store):
    """Second stream request for a completed battle replays; no new model calls."""
    battle_id = await test_store.create_battle("Hi", "general", "model-alpha", "model-beta")

    usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    chunks = [_make_mock_chunk(content="stored"), _make_mock_chunk(usage=usage)]
    counter = {"n": 0}
    client = _mock_stream_client(chunks, counter)

    with patch("app.arena.get_client", return_value=client):
        first = await _collect_events(stream_battle(test_config, test_store, battle_id))
    calls_after_first = counter["n"]
    assert calls_after_first == 2  # one per side

    # Second request must not hit the model.
    with patch("app.arena.get_client", return_value=client):
        second = await _collect_events(stream_battle(test_config, test_store, battle_id))

    assert counter["n"] == calls_after_first, "second stream must not fire new model calls"
    second_text = "".join(second)
    assert "replayed" in second_text
    assert '"reasoning_effort"' in second_text  # replayed done events carry the effort too
    assert "battle_complete" in second_text
    # Also assert the first stream really completed (baseline sanity).
    assert "battle_complete" in "".join(first)


@pytest.mark.asyncio
async def test_stream_battle_concurrent_only_runs_once(test_config, test_store):
    """Two concurrent stream requests for the same battle fire model calls exactly once."""
    battle_id = await test_store.create_battle("Hi", "general", "model-alpha", "model-beta")

    usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    counter = {"n": 0}

    async def mock_create(**kwargs):
        counter["n"] += 1
        # Yield slowly so both streams overlap in the queue-draining loop.
        chunks = [_make_mock_chunk(content="tok"), _make_mock_chunk(usage=usage)]
        mock_stream = AsyncMock()
        mock_stream.__aiter__ = lambda self: self
        mock_stream._items = list(chunks)

        async def anext_impl(self):
            await asyncio.sleep(0.01)
            if self._items:
                return self._items.pop(0)
            raise StopAsyncIteration

        mock_stream.__anext__ = anext_impl
        return mock_stream

    client = AsyncMock()
    client.chat.completions.create = mock_create

    with patch("app.arena.get_client", return_value=client):
        r1, r2 = await asyncio.gather(
            _collect_events(stream_battle(test_config, test_store, battle_id)),
            _collect_events(stream_battle(test_config, test_store, battle_id)),
        )

    # Exactly 2 model calls total (one per side, one caller). The loser sees
    # either the "already streaming" error or a replay if the first completed
    # before it hit claim_battle_execution; both outcomes leave the counter at 2.
    assert counter["n"] == 2, f"expected 2 model calls total, got {counter['n']}"

    texts = ["".join(r1), "".join(r2)]
    # One of the two must be a winning stream (has battle_complete without "replayed").
    winning = [t for t in texts if "battle_complete" in t and '"replayed": true' not in t]
    assert len(winning) == 1, f"expected exactly one non-replayed winner, got {len(winning)}"


# --- reasoning effort + opponent picking ---


def test_normalize_reasoning_effort():
    assert normalize_reasoning_effort(None) is None
    assert normalize_reasoning_effort("off") is None
    assert normalize_reasoning_effort("") is None
    assert normalize_reasoning_effort(" High ") == "high"
    with pytest.raises(ValueError, match="reasoning_effort"):
        normalize_reasoning_effort("max")


def test_completion_kwargs_respects_model_flag(test_config):
    model = test_config.get_model("model-alpha")
    assert completion_kwargs(model, None) == {}
    assert completion_kwargs(model, "low") == {"reasoning_effort": "low"}
    model.reasoning = False
    assert completion_kwargs(model, "low") == {}
    model.reasoning = None


def test_pick_opponent_never_returns_chosen(test_config):
    chosen = test_config.get_model("model-alpha")
    for _ in range(20):
        opp = pick_opponent(test_config, "general", chosen)
        assert opp.id != chosen.id
        assert "general" in opp.categories
        assert opp.enabled


def test_pick_opponent_local_chosen_prefers_gateway(test_config):
    chosen = test_config.get_model("model-local")
    for _ in range(20):
        opp = pick_opponent(test_config, "general", chosen)
        assert opp.provider_name == "test-gateway"


def test_pick_opponent_no_candidates_raises(test_config):
    chosen = test_config.get_model("model-alpha")
    with pytest.raises(ValueError, match="no other enabled model"):
        pick_opponent(test_config, "nonexistent-category", chosen)


def _mock_stream(chunks):
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

    return mock_create


def _bad_request() -> BadRequestError:
    request = httpx.Request("POST", "http://fake/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return BadRequestError("unsupported parameter: reasoning_effort", response=response, body=None)


@pytest.mark.asyncio
async def test_stream_passes_reasoning_effort_and_thinking(test_config, test_store):
    """reasoning_effort reaches the provider call; reasoning_content streams as *_thinking events."""
    battle_id = await test_store.create_battle("Hi", "general", "model-alpha", "model-beta", reasoning_effort="high")

    seen_kwargs = []

    think_chunk = MagicMock()
    choice = MagicMock()
    choice.delta.content = None
    choice.delta.reasoning_content = "let me think"
    think_chunk.choices = [choice]
    think_chunk.usage = None

    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.completion_tokens_details.reasoning_tokens = 7
    chunks = [think_chunk, _make_mock_chunk(content="answer"), _make_mock_chunk(usage=usage)]

    inner = _mock_stream(chunks)

    async def mock_create(**kwargs):
        seen_kwargs.append(kwargs)
        return await inner(**kwargs)

    mock_client = AsyncMock()
    mock_client.chat.completions.create = mock_create

    with patch("app.arena.get_client", return_value=mock_client):
        events = await _collect_events(stream_battle(test_config, test_store, battle_id))

    assert all(k.get("reasoning_effort") == "high" for k in seen_kwargs)
    text = "".join(events)
    assert "model_a_thinking" in text and "let me think" in text
    assert '"reasoning_effort": "high"' in text
    assert '"reasoning_tokens": 7' in text
    assert "battle_complete" in text


@pytest.mark.asyncio
async def test_stream_falls_back_when_provider_rejects_reasoning(test_config, test_store):
    """A 400 on reasoning_effort retries once without it and tells the client."""
    battle_id = await test_store.create_battle("Hi", "general", "model-alpha", "model-beta", reasoning_effort="low")

    calls = []
    inner = _mock_stream([_make_mock_chunk(content="plain answer")])

    async def mock_create(**kwargs):
        calls.append(kwargs)
        if "reasoning_effort" in kwargs:
            raise _bad_request()
        return await inner(**kwargs)

    mock_client = AsyncMock()
    mock_client.chat.completions.create = mock_create

    with patch("app.arena.get_client", return_value=mock_client):
        events = await _collect_events(stream_battle(test_config, test_store, battle_id))

    text = "".join(events)
    assert "reasoning_unsupported" in text
    assert "plain answer" in text
    assert '"reasoning_effort": null' in text
    assert "battle_complete" in text
    # two sides x (one rejected + one retry) = 4 calls
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_stream_no_fallback_when_model_declares_reasoning(test_config, test_store):
    """reasoning: true means a 400 is a real error, not a signal to retry without thinking."""
    battle_id = await test_store.create_battle("Hi", "general", "model-alpha", "model-beta", reasoning_effort="low")
    test_config.get_model("model-alpha").reasoning = True
    test_config.get_model("model-beta").reasoning = True

    calls = []

    async def mock_create(**kwargs):
        calls.append(kwargs)
        raise _bad_request()

    mock_client = AsyncMock()
    mock_client.chat.completions.create = mock_create

    try:
        with patch("app.arena.get_client", return_value=mock_client):
            events = await _collect_events(stream_battle(test_config, test_store, battle_id))
    finally:
        test_config.get_model("model-alpha").reasoning = None
        test_config.get_model("model-beta").reasoning = None

    text = "".join(events)
    assert "model_a_error" in text and "model_b_error" in text
    assert len(calls) == 2
