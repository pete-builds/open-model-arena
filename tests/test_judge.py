"""Tests for the LLM-as-judge module.

Only the parsing / prompt-building layer is exercised here — the OpenAI
call is mocked. End-to-end integration lives in ``test_api.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Config, Judge, Model, Provider
from app.judge import (
    JudgeError,
    _build_messages,
    _extract_verdict,
    _truncate,
    run_judge,
)

# --- Verdict extraction ---


def test_extract_verdict_plain_json():
    v = _extract_verdict('{"winner": "a", "reasoning": "A was more accurate."}')
    assert v == {"winner": "a", "reasoning": "A was more accurate."}


def test_extract_verdict_json_fence():
    raw = '```json\n{"winner": "b", "reasoning": "clearer"}\n```'
    v = _extract_verdict(raw)
    assert v["winner"] == "b"


def test_extract_verdict_bare_fence():
    raw = '```\n{"winner": "tie", "reasoning": "equivalent"}\n```'
    v = _extract_verdict(raw)
    assert v["winner"] == "tie"


def test_extract_verdict_with_surrounding_prose():
    raw = 'Here is my verdict: {"winner": "a", "reasoning": "yes"}. Thanks.'
    v = _extract_verdict(raw)
    assert v["winner"] == "a"


def test_extract_verdict_normalizes_case():
    v = _extract_verdict('{"winner": "A", "reasoning": "x"}')
    assert v["winner"] == "a"


def test_extract_verdict_rejects_invalid_winner():
    with pytest.raises(JudgeError, match="winner"):
        _extract_verdict('{"winner": "left", "reasoning": "x"}')


def test_extract_verdict_rejects_no_json():
    with pytest.raises(JudgeError, match="no JSON"):
        _extract_verdict("The winner is A.")


def test_extract_verdict_rejects_bad_json():
    with pytest.raises(JudgeError, match="parse"):
        _extract_verdict('{"winner": "a", "reasoning": ')


def test_extract_verdict_truncates_long_reasoning():
    long = "x" * 5000
    v = _extract_verdict(f'{{"winner": "a", "reasoning": "{long}"}}')
    assert len(v["reasoning"]) == 1000


# --- Prompt building ---


def test_build_messages_contains_prompt_and_both_responses():
    msgs = _build_messages("Explain closures.", "answer A", "answer B", "rubric text")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "rubric text"
    assert msgs[1]["role"] == "user"
    assert "Explain closures." in msgs[1]["content"]
    assert "answer A" in msgs[1]["content"]
    assert "answer B" in msgs[1]["content"]


def test_truncate_leaves_short_text_alone():
    assert _truncate("short", limit=100) == "short"


def test_truncate_chops_long_text():
    long = "x" * 200
    out = _truncate(long, limit=50)
    assert len(out) < 200
    assert out.endswith("[truncated]")


# --- run_judge with mocked OpenAI ---


def _cfg_with_judge() -> tuple[Config, Judge, Model]:
    prov = Provider(name="fake", base_url="http://fake/v1", api_key="k", timeout=10)
    judge_model = Model(
        id="judge-1",
        provider_name="fake",
        display_name="Fake Judge",
        model_id="fake-judge",
        input_cost_per_1m=1.0,
        output_cost_per_1m=2.0,
    )
    judge = Judge(model_id="judge-1", rubric="Score fairly.")
    config = Config(providers={"fake": prov}, models=[judge_model], judge=judge)
    return config, judge, judge_model


@pytest.mark.asyncio
async def test_run_judge_returns_parsed_verdict():
    config, judge, judge_model = _cfg_with_judge()
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"winner": "a", "reasoning": "cleaner"}'))],
        usage=SimpleNamespace(prompt_tokens=200, completion_tokens=50),
    )

    with patch("app.judge.get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=fake_response)))
        )
        result = await run_judge(config, judge, judge_model, "prompt?", "A resp", "B resp")

    assert result["winner"] == "a"
    assert result["reasoning"] == "cleaner"
    assert result["judge_model_id"] == "judge-1"
    assert result["prompt_tokens"] == 200
    assert result["completion_tokens"] == 50
    # cost = (200/1M * 1.0) + (50/1M * 2.0) = 0.0002 + 0.0001 = 0.0003
    assert result["cost"] == pytest.approx(0.0003, abs=1e-6)


@pytest.mark.asyncio
async def test_run_judge_rejects_two_empty_responses():
    config, judge, judge_model = _cfg_with_judge()
    with pytest.raises(JudgeError, match="empty"):
        await run_judge(config, judge, judge_model, "p", "", "")


@pytest.mark.asyncio
async def test_run_judge_wraps_openai_errors():
    config, judge, judge_model = _cfg_with_judge()
    with patch("app.judge.get_client") as get_client:
        get_client.return_value = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom"))))
        )
        with pytest.raises(JudgeError, match="call failed"):
            await run_judge(config, judge, judge_model, "p", "a", "b")
