"""Acceptance test 7: every output carries the §2.2 trace fields."""

from __future__ import annotations

import json
from pathlib import Path

from copilot.interpret import interpret
from copilot.interpret.postcheck import prompt_version
from copilot.llm.client import LLMError
from tests.support.helpers import ScriptedClient, completion, model_json

TRACE_FIELDS = (
    "model",
    "prompt_version",
    "approx_input_tokens",
    "approx_output_tokens",
    "latency_ms",
    "repair_attempts",
    "model_calls",
    "rule_model_disagreements",
)


def _assert_trace(output) -> None:
    trace = output.model_dump(mode="json")["trace"]
    assert set(TRACE_FIELDS) <= set(trace)
    assert "usage" not in trace
    assert trace["prompt_version"] == prompt_version()
    header = Path("copilot/interpret/prompts/interp-v2.txt").read_text(encoding="utf-8")
    assert header.startswith(f"prompt_version: {trace['prompt_version']}")
    assert trace["prompt_version"] == "interp-v2"
    assert isinstance(trace["approx_input_tokens"], int)
    assert isinstance(trace["approx_output_tokens"], int)
    assert isinstance(trace["latency_ms"], int)
    assert isinstance(trace["repair_attempts"], int)
    assert isinstance(trace["model_calls"], int)
    assert isinstance(trace["rule_model_disagreements"], list)
    assert isinstance(trace["model"], str) and trace["model"]
    assert isinstance(trace["prompt_version"], str) and trace["prompt_version"]


def test_success_and_fallback_traces_and_no_frozen_ids() -> None:
    success = interpret(
        {"query": "What parameters does the API take?"},
        client=ScriptedClient([completion(model_json())]),
    )
    fallback = interpret(
        {"query": "hello there"},
        client=ScriptedClient([LLMError("timeout")]),
    )
    _assert_trace(success)
    _assert_trace(fallback)
    assert success.model_dump(mode="json")["trace"]["model_calls"] == 1
    prompt = Path("copilot/interpret/prompts/interp-v2.txt").read_text(encoding="utf-8")
    rules = Path("copilot/interpret/rules.py").read_text(encoding="utf-8")
    assert "GT-LBS-" not in prompt
    assert "GT-LBS-" not in rules
    raw_ids = json.loads(Path("copilot/interpret/fewshot_ids.json").read_text(encoding="utf-8"))
    record_ids = raw_ids["record_ids"] if isinstance(raw_ids, dict) else raw_ids
    frozen = json.loads(Path("eval/freeze-manifest.json").read_text(encoding="utf-8"))[
        "frozen_record_ids"
    ]
    assert record_ids
    assert set(record_ids).isdisjoint(frozen)
