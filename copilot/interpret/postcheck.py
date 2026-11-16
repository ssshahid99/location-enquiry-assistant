"""Deterministic post-check for one model reply.

Purpose: strip ``<think>`` blocks, parse the first JSON object, normalise
vocabulary, drop ungrounded values, merge history and known facts, and
compute missing fields in code.
Use case: ``interpret`` calls this after every model reply and again for the
rules-only fallback.
Example: a ``<think>`` block followed by JSON and a thank-you sentence yields
the JSON object only.

``sub_intent`` values that are not in ``Vocab.intent_to_sub_intents[intent]``
are set to null and logged. The shared validator treats that mismatch as
schema-valid, so this module does the check itself.

On the rules-only path (interpretation_output v0.2.0), each of ``intent``,
``api`` and ``team`` that the rules do not support is JSON null. ``abstain``
is true and ``abstain_reason`` is the caller’s reason. ``use_case_api`` is
null when ``api`` is null. This module does not substitute a vocabulary entry.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from . import rules, seam

logger = logging.getLogger("copilot.interpret")

_THINK_CLOSED = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think>.*", re.IGNORECASE | re.DOTALL)

_QUAL_KEYS = (
    "use_case",
    "region",
    "monthly_volume",
    "current_provider",
    "timeline",
    "budget",
    "technical_constraints",
    "decision_maker",
)
_RULE_WINS = {"region", "monthly_volume", "current_provider"}
_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "interp-v2.txt"


def prompt_version() -> str:
    """Version header of ``prompts/interp-v2.txt``."""
    for line in _PROMPT_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("prompt_version:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("prompt file is missing a prompt_version header")


def default_trace() -> dict:
    """Trace object with the §2.2 keys and zero counters."""
    return {
        "model": "Qwen/Qwen3-8B",
        "prompt_version": prompt_version(),
        "approx_input_tokens": 0,
        "approx_output_tokens": 0,
        "latency_ms": 0,
        "repair_attempts": 0,
        "model_calls": 0,
        "rule_model_disagreements": [],
    }


def strip_think(text: str) -> str:
    """Remove closed ``<think>`` blocks and a trailing unclosed block."""
    stripped = _THINK_CLOSED.sub("", text or "")
    stripped = _THINK_OPEN.sub("", stripped)
    return stripped.strip()


def first_json_object(text: str) -> dict:
    """Parse the first JSON object, ignoring leading and trailing prose."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    value, _end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("first JSON value is not an object")
    return value


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold().strip()


def _as_turn(turn: Any) -> dict:
    if isinstance(turn, dict):
        return {"role": turn.get("role"), "text": turn.get("text") or ""}
    return {"role": getattr(turn, "role", None), "text": getattr(turn, "text", "") or ""}


def _as_fact(fact: Any) -> dict:
    if isinstance(fact, dict):
        return {
            "field": fact.get("field"),
            "value": fact.get("value"),
            "source": fact.get("source"),
        }
    return {
        "field": getattr(fact, "field", None),
        "value": getattr(fact, "value", None),
        "source": getattr(fact, "source", None),
    }


def _fact_blob(fact: dict) -> str:
    value = fact.get("value")
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return f"{fact.get('field') or ''}\n{rendered}"


def _locate(evidence: Any, turns: list[dict], facts: list[dict]) -> int | None:
    """Latest user-turn index containing evidence, or -1 when only a known fact does."""
    if not isinstance(evidence, str) or not evidence.strip():
        return None
    needle = _collapse(evidence)
    if not needle:
        return None
    found: int | None = None
    for index, turn in enumerate(turns):
        if needle in _collapse(turn["text"]):
            found = index
    if found is not None:
        return found
    for fact in facts:
        if needle in _collapse(_fact_blob(fact)):
            return -1
    return None


def _coerce_volume(raw: Any, evidence: Any) -> dict | None:
    from mapping.map_gt import _parse_volume_number, extract_monthly_volume

    text = raw if isinstance(raw, str) else (str(raw) if raw is not None else "")
    parsed = extract_monthly_volume(text) if text else None
    if parsed is None and isinstance(evidence, str):
        parsed = extract_monthly_volume(evidence)
    if parsed:
        return parsed
    cleaned = text.strip()
    if not cleaned:
        return None
    number = _parse_volume_number(cleaned)
    return {
        "raw": cleaned,
        "requests_per_month": number,
        "unit": "per_month" if number is not None else None,
    }


def _model_values(model: dict | None, turns: list[dict], facts: list[dict]) -> dict[str, tuple[Any, int]]:
    """Grounded, canonical model qualification values keyed by field."""
    found: dict[str, tuple[Any, int]] = {}
    if not model:
        return found
    qual = model.get("qualification")
    if not isinstance(qual, dict):
        return found
    for field in _QUAL_KEYS:
        item = qual.get(field)
        if not isinstance(item, dict):
            continue
        raw_value = item.get("value")
        evidence = item.get("evidence")
        if raw_value is None:
            continue
        if isinstance(raw_value, str) and rules.is_decline_value(raw_value):
            continue
        where = _locate(evidence, turns, facts)
        if where is None:
            continue
        if field == "current_provider":
            canonical = seam.canonical_provider(raw_value)
            if canonical is None:
                continue
            found[field] = (canonical, where)
            continue
        if field == "monthly_volume":
            volume = _coerce_volume(raw_value, evidence)
            if volume is None:
                continue
            found[field] = (volume, where)
            continue
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if text:
                found[field] = (text, where)
    return found


def _known_values(facts: list[dict]) -> dict[str, tuple[Any, str]]:
    stored: dict[str, tuple[Any, str]] = {}
    for fact in facts:
        field = fact.get("field")
        if field not in {
            "use_case_api",
            "region",
            "monthly_volume",
            "current_provider",
            "timeline",
            "budget",
            "technical_constraints",
            "decision_maker",
        }:
            continue
        provenance = "state" if fact.get("source") == "state_store" else "history"
        value = fact.get("value")
        if field == "current_provider":
            value = seam.canonical_provider(value)
        elif field == "monthly_volume":
            if isinstance(value, dict) and isinstance(value.get("raw"), str) and value.get("raw"):
                value = {
                    "raw": value["raw"],
                    "requests_per_month": value.get("requests_per_month"),
                    "unit": value.get("unit"),
                }
            elif isinstance(value, str):
                value = _coerce_volume(value, value)
            else:
                value = None
        elif field == "use_case_api":
            if isinstance(value, dict):
                api = seam.normalize_enum("api", value.get("api")) if isinstance(value.get("api"), str) else None
                use_case = value.get("use_case")
                if isinstance(use_case, str):
                    use_case = use_case.strip() or None
                else:
                    use_case = None
                value = {"api": api, "use_case": use_case} if api or use_case else None
            else:
                value = None
        elif isinstance(value, str):
            value = value.strip() or None
        elif value is None:
            value = None
        else:
            value = None
        if value is None:
            continue
        stored[field] = (value, provenance)
    return stored


def _pick(field: str, rule_value: Any, model_value: Any) -> tuple[Any, str | None]:
    if field in _RULE_WINS:
        if rule_value is not None:
            return rule_value, "rule"
        if model_value is not None:
            return model_value, "model"
        return None, None
    if model_value is not None:
        return model_value, "model"
    if rule_value is not None:
        return rule_value, "rule"
    return None, None


def _fold(
    field: str,
    turns: list[dict],
    known: dict[str, tuple[Any, str]],
    model_values: dict[str, tuple[Any, int]],
) -> tuple[Any, str | None]:
    value: Any = None
    provenance: str | None = None
    known_item = known.get(field)
    model_item = model_values.get(field)
    model_value = model_item[0] if model_item else None
    model_index = model_item[1] if model_item else None
    if known_item is not None:
        value, provenance = known_item
    elif model_index == -1 and model_value is not None:
        value, provenance = model_value, "state"
    for index, turn in enumerate(turns):
        if field in turn["declines"]:
            value, provenance = None, None
            continue
        hit = turn.get(field)
        rule_value = hit.get("value") if hit else None
        placed = model_value if model_index == index else None
        chosen, how = _pick(field, rule_value, placed)
        if chosen is None:
            continue
        value = chosen
        provenance = how if index == len(turns) - 1 else "history"
    return value, provenance


def _rule_aggregate(field: str, turns: list[dict]) -> Any:
    value = None
    for turn in turns:
        if field in turn["declines"]:
            value = None
            continue
        hit = turn.get(field)
        if hit and hit.get("value") is not None:
            value = hit["value"]
    return value


def _same(field: str, left: Any, right: Any) -> bool:
    if left is None or right is None:
        return True
    if field == "monthly_volume":
        left_n = left.get("requests_per_month") if isinstance(left, dict) else None
        right_n = right.get("requests_per_month") if isinstance(right, dict) else None
        if left_n is not None and right_n is not None:
            return int(left_n) == int(right_n)
        left_raw = left.get("raw") if isinstance(left, dict) else left
        right_raw = right.get("raw") if isinstance(right, dict) else right
        return _collapse(str(left_raw or "")) == _collapse(str(right_raw or ""))
    return _collapse(str(left)) == _collapse(str(right))


def _confidence(model: dict | None) -> dict:
    raw = model.get("confidence") if isinstance(model, dict) else None
    out = {"intent": None, "api": None, "team": None}
    if not isinstance(raw, dict):
        return out
    for key in out:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if 0 <= float(value) <= 1:
            out[key] = float(value)
    return out


def _latest(turns: list[dict], key: str) -> str | None:
    found = None
    for turn in turns:
        if turn.get(key):
            found = turn[key]
    return found


def _qualification(api: str | None, use_case: str | None, values: dict[str, Any]) -> dict:
    canonical_api = api if isinstance(api, str) and seam.normalize_enum("api", api) == api else None
    if canonical_api:
        use_case_api = {"api": canonical_api, "use_case": use_case}
    else:
        use_case_api = None
    qual = {
        "use_case_api": use_case_api,
        "region": values.get("region"),
        "monthly_volume": values.get("monthly_volume"),
        "current_provider": values.get("current_provider"),
        "timeline": values.get("timeline"),
        "budget": values.get("budget"),
        "technical_constraints": values.get("technical_constraints"),
        "decision_maker": values.get("decision_maker"),
    }
    missing = list(seam.compute_missing_required_fields(qual))
    qual["missing_required_fields"] = missing
    qual["complete"] = len(missing) == 0
    return qual


def _resolve(
    *,
    query: str,
    history: list,
    known_facts: list,
    model: dict | None,
    abstain: bool,
    abstain_reason: str | None,
    trace: dict | None,
) -> dict:
    facts = [_as_fact(item) for item in known_facts or []]
    extraction = rules.extract(history, query)
    turns = extraction["user_turns"]
    known = _known_values(facts)
    model_values = _model_values(model, turns, facts)
    disagreements: list[str] = []
    logs: list[str] = []
    vocab = seam.load_vocab()

    resolved: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for field in (
        "region",
        "monthly_volume",
        "current_provider",
        "timeline",
        "budget",
        "technical_constraints",
        "decision_maker",
        "use_case",
    ):
        value, source = _fold(field, turns, known, model_values)
        resolved[field] = value
        if value is not None and source:
            provenance[field] = source
        rule_value = _rule_aggregate(field, turns)
        model_item = model_values.get(field)
        if model_item and rule_value is not None and not _same(field, rule_value, model_item[0]):
            disagreements.append(field)

    rule_api = extraction["unique_api"]
    model_api_raw = model.get("api") if isinstance(model, dict) else None
    model_api = (
        seam.normalize_enum("api", model_api_raw) if isinstance(model_api_raw, str) else None
    )
    if rule_api and model_api and rule_api != model_api:
        disagreements.append("api")
    api_provenance = None
    if rule_api:
        api = rule_api
        current_hit = any(hit["api"] == rule_api for hit in turns[-1]["api_hits"])
        api_provenance = "rule" if current_hit else "history"
    elif model is None:
        api = None
    elif model_api:
        api = model_api
        api_provenance = "model"
    elif isinstance(model_api_raw, str) and model_api_raw != "":
        api = model_api_raw
        api_provenance = "model"
    else:
        known_api = known.get("use_case_api")
        if known_api and isinstance(known_api[0], dict) and known_api[0].get("api"):
            api = known_api[0]["api"]
            api_provenance = known_api[1]
        else:
            api = None

    rule_intent = _latest(turns, "intent")
    model_intent_raw = model.get("intent") if isinstance(model, dict) else None
    model_intent = (
        seam.normalize_enum("intent", model_intent_raw)
        if isinstance(model_intent_raw, str)
        else None
    )
    if rule_intent and model_intent and rule_intent != model_intent:
        disagreements.append("intent")
    if model is None:
        intent = rule_intent
    elif model_intent:
        intent = model_intent
    elif isinstance(model_intent_raw, str) and model_intent_raw != "":
        intent = model_intent_raw
    else:
        intent = None

    rule_team = _latest(turns, "team")
    model_team_raw = model.get("team") if isinstance(model, dict) else None
    model_team = (
        seam.normalize_enum("team", model_team_raw) if isinstance(model_team_raw, str) else None
    )
    if rule_team and model_team and rule_team != model_team:
        disagreements.append("team")
    if model is None:
        team = rule_team
    elif model_team:
        team = model_team
    elif isinstance(model_team_raw, str) and model_team_raw != "":
        team = model_team_raw
    else:
        team = None

    use_case = resolved.get("use_case") if isinstance(resolved.get("use_case"), str) else None
    if not use_case:
        known_use = known.get("use_case_api")
        if known_use and isinstance(known_use[0], dict):
            use_case = known_use[0].get("use_case")
    qual = _qualification(api if isinstance(api, str) else None, use_case, resolved)
    if qual["use_case_api"] is not None:
        if api_provenance:
            provenance["use_case_api"] = api_provenance
        elif provenance.get("use_case"):
            provenance["use_case_api"] = provenance["use_case"]

    sub_intent = None
    if isinstance(model, dict):
        raw_sub = model.get("sub_intent")
        canon_sub = seam.normalize_enum("sub_intent", raw_sub) if isinstance(raw_sub, str) else None
        parent = intent if isinstance(intent, str) and intent in vocab.intent_to_sub_intents else None
        if isinstance(raw_sub, str) and raw_sub != "" and canon_sub is None:
            message = f"sub_intent {raw_sub!r} is not in the vocabulary; set to null"
            logger.info(message)
            logs.append(message)
        elif parent and canon_sub and canon_sub not in vocab.intent_to_sub_intents[parent]:
            message = (
                f"sub_intent {canon_sub!r} is not in intent_to_sub_intents[{parent!r}]; set to null"
            )
            logger.info(message)
            logs.append(message)
        else:
            sub_intent = canon_sub

    if not abstain:
        abstain_reason = None
    disagreements = sorted(set(disagreements))
    trace_out = dict(default_trace())
    if trace:
        trace_out.update(trace)
    trace_out["rule_model_disagreements"] = list(disagreements)
    output = {
        "intent": intent,
        "sub_intent": sub_intent,
        "api": api,
        "team": team,
        "qualification_details": qual,
        "missing_required_fields": list(qual["missing_required_fields"]),
        "field_provenance": provenance,
        "confidence": _confidence(model) if model is not None else {
            "intent": None,
            "api": None,
            "team": None,
        },
        "abstain": abstain,
        "abstain_reason": abstain_reason,
        "trace": trace_out,
    }
    errors = list(seam.validate_interpretation_output(output))
    return {
        "output": output,
        "errors": errors,
        "disagreements": disagreements,
        "logs": logs,
    }


def postcheck(
    raw: str,
    *,
    query: str,
    history: list | None = None,
    known_facts: list | None = None,
    trace: dict | None = None,
) -> dict:
    """Post-check one model reply. ``errors`` empty means the output is valid."""
    try:
        parsed = first_json_object(strip_think(raw or ""))
    except (ValueError, json.JSONDecodeError) as exc:
        return {
            "output": None,
            "errors": [f"/: JSON_PARSE: {exc}"],
            "disagreements": [],
            "logs": [],
            "parsed": None,
        }
    resolved = _resolve(
        query=query,
        history=history or [],
        known_facts=known_facts or [],
        model=parsed,
        abstain=False,
        abstain_reason=None,
        trace=trace,
    )
    resolved["parsed"] = parsed
    return resolved


def rules_only(
    *,
    query: str,
    history: list | None = None,
    known_facts: list | None = None,
    abstain_reason: str,
    trace: dict | None = None,
) -> dict:
    """Rules, history and known facts only. Unsupported intent, api and team stay null."""
    resolved = _resolve(
        query=query,
        history=history or [],
        known_facts=known_facts or [],
        model=None,
        abstain=True,
        abstain_reason=abstain_reason,
        trace=trace,
    )
    resolved["parsed"] = None
    return resolved
