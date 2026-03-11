from __future__ import annotations

import asyncio
import json
import logging
import random
import time

from openai import AsyncOpenAI

from .config import Config, Model


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


async def stream_battle(config: Config, store, battle_id: str):
    """Generator that yields SSE events for both model responses."""
    battle = await store.get_battle(battle_id)
    if not battle:
        yield f"event: error\ndata: {json.dumps({'error': 'battle not found'})}\n\n"
        return

    model_a = config.get_model(battle["model_a"])
    model_b = config.get_model(battle["model_b"])

    if not model_a or not model_b:
        yield f"event: error\ndata: {json.dumps({'error': 'model not found in config'})}\n\n"
        return

    client_a = get_client(config, model_a)
    client_b = get_client(config, model_b)

    prompt = battle["prompt"]
    messages = [{"role": "user", "content": prompt}]

    results = {"a": {}, "b": {}}
    queues = {"a": asyncio.Queue(), "b": asyncio.Queue()}

    async def call_model(client: AsyncOpenAI, model: Model, side: str):
        provider = config.get_provider(model.provider_name)
        timeout_s = provider.timeout or 60
        start = time.monotonic()
        full_response = []
        usage_data = None

        async def _stream():
            nonlocal usage_data
            stream = await client.chat.completions.create(
                model=model.model_id,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                max_tokens=2048,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    delta = chunk.choices[0].delta.content
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
        }

        update = store.update_response_a if side == "a" else store.update_response_b
        await update(battle_id, response_text, elapsed_ms, output_tokens, round(cost, 6))
        await queues[side].put(("done", None))

    # Start both model calls concurrently
    task_a = asyncio.create_task(call_model(client_a, model_a, "a"))
    task_b = asyncio.create_task(call_model(client_b, model_b, "b"))

    done_a = False
    done_b = False

    while not (done_a and done_b):
        # Check both queues with a small timeout
        for side, label in [("a", "model_a"), ("b", "model_b")]:
            if (side == "a" and done_a) or (side == "b" and done_b):
                continue
            try:
                msg_type, data = queues[side].get_nowait()
                if msg_type == "token":
                    yield f"event: {label}\ndata: {json.dumps({'token': data})}\n\n"
                elif msg_type == "error":
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
