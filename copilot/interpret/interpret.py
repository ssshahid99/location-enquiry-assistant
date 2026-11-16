"""Interpretation step: rules, one model call, post-check, at most one repair.

Purpose: ``interpret(InterpretationInput) -> InterpretationOutput``.
Use case: the Planner's first node. Retrieval, tools and drafting are out of scope.
Example: a directions pricing question returns ``api="directions"`` and a
qualification object whose missing fields were computed in code.

At most two model calls. A transport failure or a second invalid reply becomes
a rules-only output with ``abstain=true``. Token counts are ``ceil(chars/4)``.
The real ``usage`` object is kept on the batch recording, not on ``trace``,
because the trace schema forbids extra keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import fewshot, postcheck, rules, seam
from copilot.llm.client import (
    DEFAULT_MODEL,
    BudgetExceeded,
    Completion,
    ConfigError,
    LLMClient,
    LLMError,
    estimate_input_tokens,
    estimate_text_tokens,
)

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "interp-v2.txt"
_MAX_HISTORY_TURNS = 6
_MAX_HISTORY_CHARS = 1600
_TEMPERATURE = 0
_MAX_TOKENS = 512


def _load_prompt() -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in _PROMPT_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = line.strip("=").strip()
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def _cap_history(turns: list[dict]) -> list[dict]:
    trimmed = list(turns)[-_MAX_HISTORY_TURNS:]
    while len(trimmed) > 1 and sum(len(turn["text"]) for turn in trimmed) > _MAX_HISTORY_CHARS:
        trimmed.pop(0)
    if trimmed and sum(len(turn["text"]) for turn in trimmed) > _MAX_HISTORY_CHARS:
        kept = dict(trimmed[-1])
        kept["text"] = kept["text"][:_MAX_HISTORY_CHARS]
        trimmed[-1] = kept
    return trimmed


def _render_history(turns: list[dict]) -> str:
    if not turns:
        return "(none)"
    return "\n".join(f"[{turn['role']}] {turn['text']}" for turn in turns)


def _render_user(template: str, data: dict, extraction: dict) -> str:
    history = _cap_history(
        [
            {"role": turn["role"], "text": turn["text"]}
            for turn in data.get("history") or []
            if turn.get("role") in {"user", "assistant"}
        ]
    )
    return (
        template.replace("<<HISTORY>>", _render_history(history))
        .replace("<<KNOWN>>", json.dumps(data.get("known_facts") or [], ensure_ascii=False, sort_keys=True))
        .replace("<<RULE_HINTS>>", json.dumps(rules.to_hints(extraction), ensure_ascii=False, sort_keys=True))
        .replace("<<MESSAGE>>", data.get("query") or "")
    )


def build_messages(data: dict, extraction: dict) -> list[dict]:
    """System prompt, runtime few-shot pairs (zero when the GT file is absent), then the user message."""
    sections = _load_prompt()
    system = sections.get("SYSTEM") or ""
    template = sections.get("USER") or "MESSAGE: <<MESSAGE>>"
    user = _render_user(template, data, extraction)
    messages = [{"role": "system", "content": system}]
    for example in fewshot.load_examples():
        rendered = _render_user(
            template,
            example,
            rules.extract(example["history"], example["query"]),
        )
        messages.append({"role": "user", "content": rendered})
        messages.append({"role": "assistant", "content": example["target"]})
    messages.append({"role": "user", "content": user})
    return messages


def _repair_messages(messages: list[dict], previous: str, errors: list[str]) -> list[dict]:
    sections = _load_prompt()
    template = sections.get("REPAIR") or "Errors:\n<<ERRORS>>"
    note = template.replace("<<ERRORS>>", "\n".join(errors))
    return list(messages) + [
        {"role": "assistant", "content": previous},
        {"role": "user", "content": note},
    ]


def _input_data(inp: Any) -> dict:
    models = seam.models()
    if isinstance(inp, dict):
        model = models.InterpretationInput.model_validate(inp)
    elif hasattr(inp, "model_dump"):
        model = inp
    else:
        raise TypeError("interpret() expects InterpretationInput or a dict")
    data = model.model_dump(mode="json")
    data["history"] = list(data.get("history") or [])
    data["known_facts"] = list(data.get("known_facts") or [])
    return data


def _publish(payload: dict):
    models = seam.models()
    obj = models.InterpretationOutput.model_validate(payload)
    errors = seam.validate_interpretation_output(obj.model_dump(mode="json"))
    if errors:
        raise RuntimeError(
            "validator rejected interpretation output: " + "; ".join(errors)
        )
    return obj


def _call(client, messages: list[dict]) -> tuple[Completion | None, str | None]:
    """Return ``(completion, failure)``.

    ``budget_exceeded`` and ``config`` are raised before a request is counted.
    ``model_unavailable`` means the attempt reached the endpoint and failed.
    """
    try:
        return (
            client.complete(messages, max_tokens=_MAX_TOKENS, temperature=_TEMPERATURE),
            None,
        )
    except BudgetExceeded:
        return None, "budget_exceeded"
    except ConfigError:
        return None, "config"
    except LLMError:
        return None, "model_unavailable"


def interpret(inp: Any, *, client=None):
    """Classify one turn and fill qualification details.

    ``client`` is injectable so tests never call the endpoint. With ``client``
    omitted, ``LLMClient`` reads ``TRACKB_API_KEY`` and ``TRACKB_CA_CERT`` and
    fails closed when they are missing.
    """
    data = _input_data(inp)
    query = data.get("query") or ""
    history = data.get("history") or []
    known = data.get("known_facts") or []
    extraction = rules.extract(history, query)
    messages = build_messages(data, extraction)
    version = postcheck.prompt_version()
    counters = {
        "model": DEFAULT_MODEL,
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_ms": 0,
        "model_calls": 0,
        "repair_attempts": 0,
    }

    def trace() -> dict:
        return {
            "model": counters["model"],
            "prompt_version": version,
            "approx_input_tokens": counters["input_tokens"],
            "approx_output_tokens": counters["output_tokens"],
            "latency_ms": counters["latency_ms"],
            "repair_attempts": counters["repair_attempts"],
            "model_calls": counters["model_calls"],
            "rule_model_disagreements": [],
        }

    def fallback(reason: str, disagreements: list[str] | None = None):
        built = postcheck.rules_only(
            query=query,
            history=history,
            known_facts=known,
            abstain_reason=reason,
            trace=trace(),
        )
        if disagreements is not None:
            built["output"]["trace"]["rule_model_disagreements"] = list(disagreements)
        return _publish(built["output"])

    if client is None:
        try:
            client = LLMClient()
        except ConfigError:
            return fallback("model_unavailable")

    completion, failure = _call(client, messages)
    if failure == "budget_exceeded":
        return fallback("budget_exceeded")
    if failure == "config" or completion is None and failure is None:
        return fallback("model_unavailable")
    if failure is not None or completion is None:
        counters["model_calls"] = 1
        return fallback("model_unavailable")
    counters["model_calls"] = 1
    counters["model"] = completion.model or DEFAULT_MODEL
    counters["latency_ms"] += completion.latency_ms
    counters["input_tokens"] += estimate_input_tokens(messages)
    counters["output_tokens"] += estimate_text_tokens(completion.text)

    checked = postcheck.postcheck(
        completion.text,
        query=query,
        history=history,
        known_facts=known,
        trace=trace(),
    )
    if not checked["errors"] and checked["output"] is not None:
        return _publish(checked["output"])

    counters["repair_attempts"] = 1
    repair = _repair_messages(messages, completion.text, checked["errors"])
    second, failure = _call(client, repair)
    if failure == "budget_exceeded":
        counters["repair_attempts"] = 0
        return fallback("budget_exceeded", checked["disagreements"])
    if failure == "config":
        counters["repair_attempts"] = 0
        return fallback("model_unavailable", checked["disagreements"])
    if failure is not None or second is None:
        counters["model_calls"] = 2
        return fallback("model_unavailable", checked["disagreements"])
    counters["model_calls"] = 2
    counters["latency_ms"] += second.latency_ms
    counters["input_tokens"] += estimate_input_tokens(repair)
    counters["output_tokens"] += estimate_text_tokens(second.text)
    if second.model:
        counters["model"] = second.model
    checked_again = postcheck.postcheck(
        second.text,
        query=query,
        history=history,
        known_facts=known,
        trace=trace(),
    )
    if not checked_again["errors"] and checked_again["output"] is not None:
        return _publish(checked_again["output"])
    # A call-level failure was already returned. Both calls returned text.
    # Parseable output with labels still unresolved is no_rule_match.
    # Two unparseable bodies are invalid_after_repair.
    parsed = checked.get("parsed") is not None or checked_again.get("parsed") is not None
    reason = "no_rule_match" if parsed else "invalid_after_repair"
    return fallback(reason, checked_again["disagreements"])
