from __future__ import annotations

import asyncio
import json
import logging
import random
import time

from openai import AsyncOpenAI, BadRequestError

from .config import REASONING_EFFORTS, Config, Model
from .store import EXEC_STATE_COMPLETE, EXEC_STATE_ERROR

log = logging.getLogger("arena")


def select_models(config: Config, category: str) -> tuple[Model, Model]:
    """Select two models for a battle. Never pairs two local models together."""
    candidates = config.enabled_models(category)
    if len(candidates) < 2:
        raise ValueError(f"need at least 2 enabled models for category '{category}', got {len(candidates)}")

    random.shuffle(candidates)

    # Try to ensure at least one gateway model
    local_providers = {name for name, p in config.providers.items() if p.local}
    gateway = [m for m in candidates if m.provider_name not in local_providers]
    local = [m for m in candidates if m.provider_name in local_providers]

    if len(gateway) >= 1 and len(local) >= 1 and random.random() < 0.4:
        # 40% chance to include a local model
        a = random.choice(gateway)
        b = random.choice(local)
    elif len(gateway) >= 2:
        pair = random.sample(gateway, 2)
        a, b = pair[0], pair[1]
    elif len(gateway) >= 1 and len(local) >= 1:
        a = random.choice(gateway)
        b = random.choice(local)
    else:
        # Fallback: any two different models
        pair = random.sample(candidates, 2)
        a, b = pair[0], pair[1]

    # Randomly assign to A/B so position isn't biased
    if random.random() < 0.5:
        a, b = b, a

    return a, b


def pick_opponent(config: Config, category: str, chosen: Model) -> Model:
    """Pick a random opponent for one explicitly chosen model.

    Same rules as ``select_models``: enabled, in the category, never the same
    model, and never two local models against each other when a gateway
    model is available.
    """
    candidates = [m for m in config.enabled_models(category) if m.id != chosen.id]
    if not candidates:
        raise ValueError(f"no other enabled model in category '{category}' to face {chosen.id}")
    local_providers = {name for name, p in config.providers.items() if p.local}
    if chosen.provider_name in local_providers:
        gateway = [m for m in candidates if m.provider_name not in local_providers]
        if gateway:
            candidates = gateway
    return random.choice(candidates)


def normalize_reasoning_effort(value: str | None) -> str | None:
    """Map the request field to a provider value: None/'off' → None, else one of REASONING_EFFORTS."""
    if value is None:
        return None
    value = value.strip().lower()
    if value in ("", "off", "none", "default"):
        return None
    if value not in REASONING_EFFORTS:
        raise ValueError(f"reasoning_effort must be one of: off, {', '.join(REASONING_EFFORTS)}")
    return value


def completion_kwargs(model: Model, reasoning_effort: str | None) -> dict:
    """Extra chat-completion kwargs for a model, honoring its ``reasoning`` setting."""
    if reasoning_effort and model.reasoning is not False:
        return {"reasoning_effort": reasoning_effort}
    return {}


def _reasoning_delta(delta) -> str | None:
    """Pull streamed thinking text off a delta, whichever field the provider uses.

    LiteLLM and DeepSeek send ``reasoning_content``; a few gateways send
    ``reasoning``. Both are extra fields on the SDK model, so read them
    defensively and only accept real strings (mocks and None fall through).
    """
    for attr in ("reasoning_content", "reasoning"):
        val = getattr(delta, attr, None)
        if isinstance(val, str) and val:
            return val
    return None


def _reasoning_tokens(usage) -> int:
    details = getattr(usage, "completion_tokens_details", None)
    val = getattr(details, "reasoning_tokens", None) if details is not None else None
    return val if isinstance(val, int) else 0


def get_client(config: Config, model: Model) -> AsyncOpenAI:
    provider = config.get_provider(model.provider_name)
    return AsyncOpenAI(
        base_url=provider.base_url,
        api_key=provider.api_key or "none",
        timeout=provider.timeout,
    )


def estimate_cost(model: Model, config: Config, input_tokens: int, output_tokens: int) -> float:
    provider = config.get_provider(model.provider_name)
    input_cost = (input_tokens / 1_000_000) * model.input_cost_per_1m
    output_cost = (output_tokens / 1_000_000) * model.output_cost_per_1m
    return input_cost + output_cost + provider.request_surcharge


async def run_battle_headless(config: Config, store, battle_id: str) -> dict:
    """Run both model calls to completion without streaming and persist results.

    Returns ``{"a": {...}, "b": {...}}`` where each side has response, latency_ms,
    tokens, cost. Sides with an error carry ``{"error": "..."}`` and no other keys.
    Used by the suite runner where SSE isn't wanted.

    Idempotent: claims execution before firing model calls. If the battle
    already ran (or is running elsewhere), raises ValueError instead of
    re-issuing paid requests.
    """
    battle = await store.get_battle(battle_id)
    if not battle:
        raise ValueError("battle not found")
    model_a = config.get_model(battle["model_a"])
    model_b = config.get_model(battle["model_b"])
    if not model_a or not model_b:
        raise ValueError("model not found in config")

    claimed, state = await store.claim_battle_execution(battle_id)
    if not claimed:
        raise ValueError(f"battle not claimable for execution (state={state})")

    prompt = battle["prompt"]
    messages = [{"role": "user", "content": prompt}]
    effort = battle.get("reasoning_effort")

    async def _one(model: Model, side: str) -> dict:
        client = get_client(config, model)
        provider = config.get_provider(model.provider_name)
        timeout_s = provider.timeout or 60
        start = time.monotonic()
        extra = completion_kwargs(model, effort)
        try:
            try:
                resp = await client.chat.completions.create(
                    model=model.model_id,
                    messages=messages,
                    max_tokens=2048,
                    timeout=timeout_s,
                    **extra,
                )
            except BadRequestError:
                if not extra or model.reasoning is True:
                    raise
                # Provider rejected reasoning_effort: retry once without it.
                log.warning("model %s rejected reasoning_effort=%s; retrying without", model.id, effort)
                resp = await client.chat.completions.create(
                    model=model.model_id,
                    messages=messages,
                    max_tokens=2048,
                    timeout=timeout_s,
                )
        except Exception as e:
            return {"error": str(e)}
        elapsed_ms = int((time.monotonic() - start) * 1000)
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or int(len(prompt.split()) * 1.3)
        completion_tokens = getattr(usage, "completion_tokens", 0) or int(len(text.split()) * 1.3)
        cost = estimate_cost(model, config, prompt_tokens, completion_tokens)
        update = store.update_response_a if side == "a" else store.update_response_b
        await update(battle_id, text, elapsed_ms, completion_tokens, round(cost, 6))
        return {
            "response": text,
            "latency_ms": elapsed_ms,
            "tokens": completion_tokens,
            "cost": round(cost, 6),
        }

    try:
        result_a, result_b = await asyncio.gather(_one(model_a, "a"), _one(model_b, "b"))
    except Exception:
        await store.mark_battle_execution(battle_id, EXEC_STATE_ERROR)
        raise

    terminal = EXEC_STATE_ERROR if (result_a.get("error") or result_b.get("error")) else EXEC_STATE_COMPLETE
    await store.mark_battle_execution(battle_id, terminal)
    return {"a": result_a, "b": result_b}


def _replay_side_event(battle: dict, side: str) -> str:
    """Build a *_done SSE event from responses already persisted on the row."""
    label = "model_a" if side == "a" else "model_b"
    payload = {
        "response": battle[f"response_{side}"] or "",
        "latency_ms": battle[f"latency_{side}_ms"] or 0,
        "tokens": battle[f"tokens_{side}"] or 0,
        "cost": battle[f"cost_{side}"] or 0.0,
        "reasoning_effort": battle.get("reasoning_effort"),
        "replayed": True,
    }
    return f"event: {label}_done\ndata: {json.dumps(payload)}\n\n"


async def stream_battle(config: Config, store, battle_id: str):
    """Generator that yields SSE events for both model responses.

    Idempotent: a battle can only be executed once. Concurrent stream requests
    for the same battle_id do not multiply paid provider calls — the first
    caller claims execution, subsequent callers either replay the stored
    responses (if execution already completed) or receive an error event.
    """
    battle = await store.get_battle(battle_id)
    if not battle:
        yield f"event: error\ndata: {json.dumps({'error': 'battle not found'})}\n\n"
        return

    model_a = config.get_model(battle["model_a"])
    model_b = config.get_model(battle["model_b"])

    if not model_a or not model_b:
        yield f"event: error\ndata: {json.dumps({'error': 'model not found in config'})}\n\n"
        return

    # Atomically claim execution before spending a single token upstream. If we
    # can't claim, the battle is running elsewhere, already completed, already
    # voted, or previously errored — none of those should trigger fresh calls.
    claimed, state = await store.claim_battle_execution(battle_id)
    if not claimed:
        if state == "complete":
            fresh = await store.get_battle(battle_id)
            if fresh:
                yield _replay_side_event(fresh, "a")
                yield _replay_side_event(fresh, "b")
                yield f"event: battle_complete\ndata: {json.dumps({'battle_id': battle_id, 'replayed': True})}\n\n"
                return
        if state == "running":
            msg = "battle is already streaming"
        elif state == "voted":
            msg = "battle already voted"
        elif state == "error":
            msg = "previous stream errored; battle cannot be replayed"
        else:
            msg = "battle not available for streaming"
        yield f"event: error\ndata: {json.dumps({'error': msg})}\n\n"
        return

    client_a = get_client(config, model_a)
    client_b = get_client(config, model_b)

    prompt = battle["prompt"]
    messages = [{"role": "user", "content": prompt}]
    effort = battle.get("reasoning_effort")

    results = {"a": {}, "b": {}}
    queues = {"a": asyncio.Queue(), "b": asyncio.Queue()}

    async def call_model(client: AsyncOpenAI, model: Model, side: str):
        provider = config.get_provider(model.provider_name)
        timeout_s = provider.timeout or 60
        start = time.monotonic()
        full_response = []
        thinking = []
        usage_data = None
        extra = completion_kwargs(model, effort)
        applied_effort = effort if extra else None

        async def _open_stream(kwargs: dict):
            return await client.chat.completions.create(
                model=model.model_id,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                max_tokens=2048,
                **kwargs,
            )

        async def _stream():
            nonlocal usage_data, applied_effort
            try:
                stream = await _open_stream(extra)
            except BadRequestError:
                if not extra or model.reasoning is True:
                    raise
                # Provider rejected reasoning_effort: retry once without it so
                # a non-thinking model still answers instead of erroring out.
                log.warning("model %s rejected reasoning_effort=%s; retrying without", model.id, effort)
                applied_effort = None
                await queues[side].put(("reasoning_unsupported", None))
                stream = await _open_stream({})
            async for chunk in stream:
                if chunk.choices:
                    delta_obj = chunk.choices[0].delta
                    think = _reasoning_delta(delta_obj)
                    if think:
                        thinking.append(think)
                        await queues[side].put(("thinking", think))
                    if delta_obj.content:
                        delta = delta_obj.content
                        full_response.append(delta)
                        await queues[side].put(("token", delta))
                if chunk.usage:
                    usage_data = chunk.usage

        try:
            await asyncio.wait_for(_stream(), timeout=timeout_s)
        except asyncio.TimeoutError:
            await queues[side].put(("error", f"timed out after {timeout_s}s"))
            return
        except Exception as e:
            # Sanitize error: never forward raw exception text to the client,
            # as it may contain internal URLs, API keys, or stack traces.
            error_type = type(e).__name__
            safe_msg = f"model call failed ({error_type})"
            logging.getLogger("arena").warning("stream error side=%s model=%s: %s", side, model.id, e)
            await queues[side].put(("error", safe_msg))
            return

        elapsed_ms = int((time.monotonic() - start) * 1000)
        response_text = "".join(full_response)

        # Use real token counts from API if available, fall back to estimates
        if usage_data and usage_data.prompt_tokens and usage_data.completion_tokens:
            input_tokens = usage_data.prompt_tokens
            output_tokens = usage_data.completion_tokens
        else:
            input_tokens = int(len(prompt.split()) * 1.3)
            output_tokens = max(len(full_response), int(len(response_text.split()) * 1.3))
        cost = estimate_cost(model, config, input_tokens, output_tokens)

        results[side] = {
            "response": response_text,
            "latency_ms": elapsed_ms,
            "tokens": output_tokens,
            "cost": round(cost, 6),
            "reasoning_effort": applied_effort,
            "reasoning_tokens": _reasoning_tokens(usage_data),
            "thinking_chars": sum(len(t) for t in thinking),
        }

        update = store.update_response_a if side == "a" else store.update_response_b
        await update(battle_id, response_text, elapsed_ms, output_tokens, round(cost, 6))
        await queues[side].put(("done", None))

    # Start both model calls concurrently
    task_a = asyncio.create_task(call_model(client_a, model_a, "a"))
    task_b = asyncio.create_task(call_model(client_b, model_b, "b"))

    done_a = False
    done_b = False
    errored = False

    try:
        while not (done_a and done_b):
            # Check both queues with a small timeout
            for side, label in [("a", "model_a"), ("b", "model_b")]:
                if (side == "a" and done_a) or (side == "b" and done_b):
                    continue
                try:
                    msg_type, data = queues[side].get_nowait()
                    if msg_type == "token":
                        yield f"event: {label}\ndata: {json.dumps({'token': data})}\n\n"
                    elif msg_type == "thinking":
                        yield f"event: {label}_thinking\ndata: {json.dumps({'token': data})}\n\n"
                    elif msg_type == "reasoning_unsupported":
                        yield f"event: {label}_notice\ndata: {json.dumps({'notice': 'reasoning_unsupported'})}\n\n"
                    elif msg_type == "error":
                        errored = True
                        yield f"event: {label}_error\ndata: {json.dumps({'error': data})}\n\n"
                        if side == "a":
                            done_a = True
                        else:
                            done_b = True
                    elif msg_type == "done":
                        yield f"event: {label}_done\ndata: {json.dumps(results[side])}\n\n"
                        if side == "a":
                            done_a = True
                        else:
                            done_b = True
                except asyncio.QueueEmpty:
                    pass

            if not (done_a and done_b):
                await asyncio.sleep(0.02)

        await task_a
        await task_b

        yield f"event: battle_complete\ndata: {json.dumps({'battle_id': battle_id})}\n\n"
    finally:
        # Always release the execution claim so the row does not sit in
        # 'running' forever if the client disconnects mid-stream.
        try:
            terminal = EXEC_STATE_ERROR if errored else EXEC_STATE_COMPLETE
            await store.mark_battle_execution(battle_id, terminal)
        except Exception:
            logging.getLogger("arena").exception("failed to mark battle %s terminal execution state", battle_id)
