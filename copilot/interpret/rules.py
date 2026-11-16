"""Deterministic pre-extraction for the interpretation step.

Purpose: pull qualification spans and API keyword hits from user turns only.
Use case: the hits are prompt hints and, on the rules-only path, the output.
Example: ``"20,000 requests a month"`` becomes a monthly_volume hit of 20000.

Patterns for volume, timeline, provider, region, budget, constraints, and
use case are the eval-freeze-v2 mapper patterns. Intent keywords are loaded
from ``intent_keywords.json`` and apply only on the rules-only (abstain) path,
matched against the customer's quoted message. Team keywords stay empty, so
the fallback leaves team null.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mapping.map_gt import (
    CURRENT_PROVIDER_CUES,
    _EVALUATION_PREFIX,
    _canon_provider,
    extract_monthly_volume,
    extract_timeline,
)

_REGION_PATTERNS = (
    re.compile(r"\b(?:in|across|for|throughout)\s+India\b", re.I),
    re.compile(r"\bIndian\s+(?:cities|city|market|price|pricing)\b", re.I),
    re.compile(r"\bIndia(?:n)?\s+price list\b", re.I),
)

_BUDGET = re.compile(
    r"\b(?:budget(?:\s+of)?|we can spend|no more than|under)\s+"
    r"(₹\s*[\d,.]+|USD\s*[\d,.]+|\$\s*[\d,.]+|[₹$][\d,.]+)",
    re.I,
)

_CONSTRAINT = re.compile(
    r"\b(\d+\s+requests per second|\d+\s+rps|self-host(?:ed|ing)?|"
    r"on-prem(?:ise)?|no live traffic|without traffic)\b",
    re.I,
)

_USE_CASE_A = re.compile(r"\bWe are an?\s+([^.?\n]{3,80})")
_USE_CASE_B = re.compile(
    r"\bOur\s+((?:commercial\s+)?(?:delivery|logistics|ride[- ]?hailing|"
    r"navigation|fleet|dispatch)[^.,?\n]{0,60})",
    re.I,
)

# Design §3: directions/routing, distance matrix, autocomplete, reverse geocod*.
_API_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("directions", re.compile(r"\b(?:directions?|routing)\b", re.I)),
    ("distance_matrix", re.compile(r"\bdistance\s+matrix\b", re.I)),
    ("autocomplete", re.compile(r"\bautocomplete\b", re.I)),
    ("reverse_geocode", re.compile(r"\breverse\s+geocod", re.I)),
)

_DECLINE = re.compile(
    r"\b(?:not\s+sure|unsure|leave\s+(?:that|it|this|the\s+\w+|\w+)\s+open|"
    r"don'?t\s+know|do\s+not\s+know)\b",
    re.I,
)

_FIELD_CUES = {
    "region": re.compile(r"\bregions?\b", re.I),
    "monthly_volume": re.compile(r"\b(?:monthly\s+volume|volumes?|requests?\s+per\s+month)\b", re.I),
    "current_provider": re.compile(r"\b(?:current\s+provider|providers?)\b", re.I),
    "timeline": re.compile(r"\b(?:timelines?|deadlines?|go[- ]?live)\b", re.I),
    "budget": re.compile(r"\bbudgets?\b", re.I),
    "technical_constraints": re.compile(r"\b(?:technical\s+constraints?|constraints?)\b", re.I),
    "decision_maker": re.compile(r"\bdecision[- ]?makers?\b", re.I),
    "use_case": re.compile(r"\buse[- ]?cases?\b", re.I),
}

# Intent fallback table (intent_keywords.json): built from the docs and the tune
# split of the §7 pool only. It is used ONLY on the rules-only (abstain) path; on
# the model path the model's intent wins (design §3). TEAM_KEYWORDS stays empty.
_KW_PATH = Path(__file__).resolve().parent / "intent_keywords.json"
_WRAPPED = re.compile(r'(?:Customer wrote|Customer message):\s*["\u201c](.+?)["\u201d]', re.S)


def _load_intent_keywords() -> list[tuple[re.Pattern[str], str]]:
    data = json.loads(_KW_PATH.read_text(encoding="utf-8"))
    return [(re.compile(pattern, re.I | re.M), label) for label, pattern in data["rules"]]


INTENT_KEYWORDS: list[tuple[re.Pattern[str], str]] = _load_intent_keywords()
TEAM_KEYWORDS: list[tuple[re.Pattern[str], str]] = []


def customer_text(text: str) -> str:
    """The quoted customer message inside a CRM/ticket wrapper, else the text itself."""
    match = _WRAPPED.search(text or "")
    return match.group(1) if match else (text or "")


def is_decline_value(text: str) -> bool:
    """True when the whole phrase declines a fact and states no positive value."""
    if not text or not isinstance(text, str):
        return False
    if not _DECLINE.search(text):
        return False
    if extract_timeline(text) or extract_monthly_volume(text):
        return False
    return len(text.strip()) <= 120


def _field_cues(text: str) -> set[str]:
    return {field for field, cre in _FIELD_CUES.items() if cre.search(text or "")}


def _named_declines(text: str) -> set[str]:
    if not _DECLINE.search(text or ""):
        return set()
    return _field_cues(text)


def _region_hit(text: str) -> dict | None:
    for pattern in _REGION_PATTERNS:
        match = pattern.search(text)
        if match:
            return {"value": "India", "span": match.group(0)}
    return None


def _budget_hit(text: str) -> dict | None:
    match = _BUDGET.search(text)
    if not match:
        return None
    return {"value": match.group(0).strip(), "span": match.group(0).strip()}


def _constraint_hit(text: str) -> dict | None:
    match = _CONSTRAINT.search(text)
    if not match:
        return None
    return {"value": match.group(0).strip(), "span": match.group(0).strip()}


def _use_case_hit(text: str) -> dict | None:
    match = _USE_CASE_A.search(text)
    if match:
        value = match.group(1).strip().rstrip(",")
        return {"value": value, "span": match.group(0).strip()}
    match = _USE_CASE_B.search(text)
    if match:
        value = match.group(0)[4:].strip()
        return {"value": value, "span": match.group(0).strip()}
    return None


def _timeline_hit(text: str) -> dict | None:
    value = extract_timeline(text)
    if not value:
        return None
    return {"value": value.strip(), "span": value.strip()}


def _volume_hit(text: str) -> dict | None:
    parsed = extract_monthly_volume(text)
    if not parsed:
        return None
    return {"value": parsed, "span": parsed["raw"]}


def _provider_hit(text: str) -> dict | None:
    best: tuple[int, str, str] | None = None
    for _cue, pattern in CURRENT_PROVIDER_CUES:
        for match in pattern.finditer(text):
            if _EVALUATION_PREFIX.search(text[max(0, match.start() - 20):match.start()]):
                continue
            canonical = _canon_provider(match.group("prov"))
            if canonical and (best is None or match.start() < best[0]):
                best = (match.start(), canonical, match.group("prov"))
    if best is None:
        return None
    return {"value": best[1], "span": best[2]}


def _api_hits(text: str) -> list[dict]:
    hits = []
    for name, pattern in _API_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append({"api": name, "span": match.group(0)})
    return hits


def _keyword(text: str, table: list[tuple[re.Pattern[str], str]]) -> str | None:
    for pattern, label in table:
        if pattern.search(text):
            return label
    return None


def _extract_user_text(text: str) -> dict:
    return {
        "text": text,
        "region": _region_hit(text),
        "monthly_volume": _volume_hit(text),
        "current_provider": _provider_hit(text),
        "timeline": _timeline_hit(text),
        "budget": _budget_hit(text),
        "technical_constraints": _constraint_hit(text),
        "use_case": _use_case_hit(text),
        "api_hits": _api_hits(text),
        "declines": _named_declines(text),
        "intent": _keyword(customer_text(text), INTENT_KEYWORDS),
        "team": _keyword(text, TEAM_KEYWORDS),
    }


def _as_turn(turn: Any) -> dict:
    if isinstance(turn, dict):
        return {"role": turn.get("role"), "text": turn.get("text") or ""}
    return {"role": getattr(turn, "role", None), "text": getattr(turn, "text", "") or ""}


def extract(history: list[Any], query: str) -> dict:
    """Extract from user turns only. ``history`` is oldest first and may include assistant turns.

    An unscoped decline ("leave that open") is applied to a field named in the
    immediately previous assistant turn. Assistant text is never mined for values.
    """
    user_turns: list[dict] = []
    previous_assistant: str | None = None
    for raw in history or []:
        turn = _as_turn(raw)
        if turn["role"] == "assistant":
            previous_assistant = turn["text"]
            continue
        if turn["role"] != "user":
            continue
        parsed = _extract_user_text(turn["text"])
        if is_decline_value(turn["text"]) and not parsed["declines"] and previous_assistant:
            parsed["declines"] = _field_cues(previous_assistant)
        user_turns.append(parsed)
        previous_assistant = None
    current = _extract_user_text(query or "")
    if is_decline_value(query or "") and not current["declines"] and previous_assistant:
        current["declines"] = _field_cues(previous_assistant)
    user_turns.append(current)

    found: list[str] = []
    all_hits: list[dict] = []
    for index, turn in enumerate(user_turns):
        for hit in turn["api_hits"]:
            all_hits.append({"api": hit["api"], "span": hit["span"], "turn": index})
            if hit["api"] not in found:
                found.append(hit["api"])
    from . import seam

    order = list(seam.load_vocab().apis)
    ordered = [name for name in order if name in set(found)]
    unique = ordered[0] if len(ordered) == 1 else None
    return {
        "user_turns": user_turns,
        "api_hits": all_hits,
        "api_families": ordered,
        "unique_api": unique,
    }


def to_hints(extraction: dict) -> dict:
    """JSON-safe rule hits for the prompt. Values are spans, not invented labels."""
    hints: dict[str, Any] = {}
    if extraction["api_hits"]:
        hints["api"] = extraction["api_hits"]
    fields = (
        "region",
        "monthly_volume",
        "current_provider",
        "timeline",
        "budget",
        "technical_constraints",
        "use_case",
    )
    for field in fields:
        spans = []
        for index, turn in enumerate(extraction["user_turns"]):
            hit = turn.get(field)
            if not hit:
                continue
            value = hit["value"]
            if isinstance(value, dict):
                value = value.get("raw")
            spans.append({"turn": index, "span": hit["span"], "value": value})
        if spans:
            hints[field] = spans
    if any(turn["declines"] for turn in extraction["user_turns"]):
        hints["declines"] = [
            {"turn": index, "fields": sorted(turn["declines"])}
            for index, turn in enumerate(extraction["user_turns"])
            if turn["declines"]
        ]
    return hints
