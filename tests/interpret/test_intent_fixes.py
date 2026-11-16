"""Intent fixes a, b and c. Fixtures are synthetic; no ground-truth question text."""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path

import pytest

from copilot.interpret import fewshot, seam
from copilot.interpret.fewshot import FewshotPoolError
from copilot.interpret.interpret import build_messages
from copilot.interpret.rules import (
    INTENT_KEYWORDS,
    customer_text,
    extract,
    _keyword,
)
from copilot.llm.client import LLMError, estimate_input_tokens
from copilot.interpret import interpret
from tests.support.helpers import ScriptedClient, completion, model_json

_INTENTS = (
    "pricing_quote",
    "pricing_comparison",
    "sales_qualification",
    "capability_match",
    "coverage_limits",
    "docs_how_to",
    "troubleshooting",
    "human_handoff",
)
_CSV_FIELDS = ("id", "question", "split", "frozen_core", "template_family")


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _gold(
    record_id: str,
    *,
    intent: str = "docs_how_to",
    sub_intent: str = "parameter_question",
    frozen_core: bool = False,
    split: str = "eval",
    use_case: str | None = None,
    region: str | None = None,
    monthly_volume: dict | None = None,
    timeline: str | None = None,
) -> dict:
    return {
        "record_id": record_id,
        "intent": intent,
        "sub_intent": sub_intent,
        "api": "directions",
        "team": "Support",
        "frozen_core": frozen_core,
        "split": split,
        "outputs": {
            "qualification_details": {
                "use_case_api": {"api": "directions", "use_case": use_case},
                "region": region,
                "monthly_volume": monthly_volume,
                "current_provider": None,
                "timeline": timeline,
                "budget": None,
                "technical_constraints": None,
                "decision_maker": None,
            }
        },
    }


def _write_gold(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _bind(monkeypatch, ids: list[str], csv_path: Path, gold_path: Path) -> None:
    monkeypatch.setenv("TRACKB_GT_CSV", str(csv_path))
    monkeypatch.setenv("TRACKB_FEWSHOT_GOLD", str(gold_path))
    monkeypatch.setattr(fewshot, "fewshot_ids", lambda path=fewshot.IDS_PATH: list(ids))
    fewshot._load.cache_clear()


def _messages_for(query: str, history: list | None = None) -> list[dict]:
    data = {"query": query, "history": history or [], "known_facts": []}
    return build_messages(data, extract(history or [], query))


def test_fewshot_absent_gt_is_zero_shot(monkeypatch, caplog) -> None:
    monkeypatch.delenv("TRACKB_GT_CSV", raising=False)
    fewshot._load.cache_clear()
    ids = fewshot.fewshot_ids()
    assert ids
    with caplog.at_level(logging.WARNING, logger="copilot.interpret"):
        messages = _messages_for("hello there")
    assert [message["role"] for message in messages] == ["system", "user"]
    expected = (
        f"few-shot: TRACKB_GT_CSV unset or missing; running zero-shot ({len(ids)} ids skipped)"
    )
    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert warnings.count(expected) == 1


def test_fewshot_loads_pairs_from_synthetic_csv(tmp_path, monkeypatch) -> None:
    ids = ["GT-TEST-001", "GT-TEST-002"]
    question = "We route city deliveries in India and send 20,000 requests."
    _write_csv(
        tmp_path / "gt.csv",
        [
            {
                "id": "GT-TEST-001",
                "question": question,
                "split": "eval",
                "frozen_core": "false",
                "template_family": "synthetic-quote",
            },
            {
                "id": "GT-TEST-002",
                "question": "How do we set the radius parameter?",
                "split": "eval",
                "frozen_core": "false",
                "template_family": "synthetic-docs",
            },
        ],
    )
    _write_gold(
        tmp_path / "gold.jsonl",
        [
            _gold(
                "GT-TEST-001",
                intent="pricing_quote",
                sub_intent="pricing_quote",
                use_case="city deliveries",
                region="India",
                monthly_volume={
                    "raw": "20,000 requests",
                    "requests_per_month": 20000,
                    "unit": "per_month",
                },
                timeline="next quarter",
            ),
            _gold("GT-TEST-002", region="India"),
        ],
    )
    _bind(monkeypatch, ids, tmp_path / "gt.csv", tmp_path / "gold.jsonl")
    messages = _messages_for("hello there")
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    first = json.loads(messages[2]["content"])
    second = json.loads(messages[4]["content"])
    assert first["intent"] == "pricing_quote"
    assert first["qualification"]["region"] == {"value": "India", "evidence": "India"}
    assert first["qualification"]["use_case"] == {
        "value": "city deliveries",
        "evidence": "city deliveries",
    }
    assert first["qualification"]["monthly_volume"] == {
        "value": "20,000 requests",
        "evidence": "20,000 requests",
    }
    assert first["qualification"]["timeline"] == {"value": None, "evidence": None}
    assert first["confidence"] == {"intent": None, "api": None, "team": None}
    assert second["intent"] == "docs_how_to"
    assert second["qualification"]["region"] == {"value": None, "evidence": None}
    assert question in messages[1]["content"]


def test_fewshot_rejects_frozen_or_holdout_id(tmp_path, monkeypatch) -> None:
    cases = [
        (
            "GT-TEST-FROZEN",
            _gold("GT-TEST-FROZEN", frozen_core=True),
            [
                {
                    "id": "GT-TEST-FROZEN",
                    "question": "How do we set a parameter?",
                    "split": "eval",
                    "frozen_core": "false",
                    "template_family": "synthetic-frozen-core",
                }
            ],
        ),
        (
            "GT-TEST-HOLD",
            _gold("GT-TEST-HOLD", split="holdout"),
            [
                {
                    "id": "GT-TEST-HOLD",
                    "question": "How do we set a parameter?",
                    "split": "holdout",
                    "frozen_core": "false",
                    "template_family": "synthetic-holdout",
                }
            ],
        ),
        (
            "GT-TEST-FAM",
            _gold("GT-TEST-FAM"),
            [
                {
                    "id": "GT-TEST-FAM",
                    "question": "How do we set a parameter?",
                    "split": "eval",
                    "frozen_core": "false",
                    "template_family": "locked-family",
                },
                {
                    "id": "GT-TEST-MARKER",
                    "question": "marker only",
                    "split": "eval",
                    "frozen_core": "true",
                    "template_family": "locked-family",
                },
            ],
        ),
    ]
    for record_id, gold, rows in cases:
        folder = tmp_path / record_id
        folder.mkdir()
        _write_csv(folder / "gt.csv", rows)
        _write_gold(folder / "gold.jsonl", [gold])
        _bind(monkeypatch, [record_id], folder / "gt.csv", folder / "gold.jsonl")
        with pytest.raises(FewshotPoolError, match=record_id):
            fewshot.load_examples()


def test_fewshot_history_split(tmp_path, monkeypatch) -> None:
    query, history = fewshot.split_question(
        "[user] hello\ncontinued line\n[assistant] ok\n[user] final ask"
    )
    assert query == "final ask"
    assert history == [
        {"role": "user", "text": "hello\ncontinued line"},
        {"role": "assistant", "text": "ok"},
    ]

    long_turn = "A" * 2000
    lines = [f"[user] marker-{index}" for index in range(8)]
    lines.append("[assistant] noted")
    lines.append(f"[user] {long_turn}")
    lines.append("[user] final routing ask")
    question = "\n".join(lines)
    split_query, split_history = fewshot.split_question(question)
    assert split_query == "final routing ask"
    assert split_history[0] == {"role": "user", "text": "marker-0"}
    assert split_history[-1] == {"role": "user", "text": long_turn}
    assert {"role": "assistant", "text": "noted"} in split_history

    record_id = "GT-TEST-HIST"
    _write_csv(
        tmp_path / "gt.csv",
        [
            {
                "id": record_id,
                "question": question,
                "split": "eval",
                "frozen_core": "false",
                "template_family": "synthetic-history",
            }
        ],
    )
    _write_gold(tmp_path / "gold.jsonl", [_gold(record_id)])
    _bind(monkeypatch, [record_id], tmp_path / "gt.csv", tmp_path / "gold.jsonl")
    messages = _messages_for("hello there")
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    rendered = messages[1]["content"]
    history_text = rendered.split("KNOWN:", 1)[0]
    assert "MESSAGE: final routing ask" in rendered
    assert "A" * 1600 in history_text
    assert "A" * 1601 not in history_text
    assert "marker-0" not in history_text
    assert "noted" not in history_text


def test_keyword_fallback_only_on_abstain() -> None:
    query = "What is the directions price?"
    assert extract([], query)["user_turns"][-1]["intent"] == "pricing_quote"
    agreed = interpret(
        {"query": query},
        client=ScriptedClient(
            [
                completion(
                    model_json(
                        intent="docs_how_to",
                        sub_intent="parameter_question",
                        api="directions",
                        team="Support",
                    )
                )
            ]
        ),
    )
    body = agreed.model_dump(mode="json")
    assert seam.validate_interpretation_output(body) == []
    assert body["intent"] == "docs_how_to"
    assert body["abstain"] is False
    assert "intent" in body["trace"]["rule_model_disagreements"]

    failed = interpret({"query": query}, client=ScriptedClient([LLMError("timeout")]))
    fallback = failed.model_dump(mode="json")
    assert seam.validate_interpretation_output(fallback) == []
    assert fallback["intent"] == "pricing_quote"
    assert fallback["abstain"] is True
    assert fallback["team"] is None


def test_keyword_wrapper_uses_quoted_message() -> None:
    query = (
        'Sandbox CRM ticket 42. Customer message: "The call returns ZERO_RESULTS for a known place." '
        "Who should own this production outage? We are escalating."
    )
    assert _keyword(query, INTENT_KEYWORDS) == "human_handoff"
    assert _keyword(customer_text(query), INTENT_KEYWORDS) == "troubleshooting"
    failed = interpret({"query": query}, client=ScriptedClient([LLMError("timeout")]))
    body = failed.model_dump(mode="json")
    assert seam.validate_interpretation_output(body) == []
    assert body["intent"] == "troubleshooting"
    assert body["team"] is None
    assert body["abstain"] is True


def test_intent_keywords_table_valid() -> None:
    data = json.loads(
        Path("copilot/interpret/intent_keywords.json").read_text(encoding="utf-8")
    )
    vocab = set(seam.load_vocab().intents)
    assert data["rules"]
    assert len(INTENT_KEYWORDS) == len(data["rules"])
    for label, pattern in data["rules"]:
        assert label in vocab
        re.compile(pattern, re.I | re.M)


def test_prompt_budget_with_max_fewshots(tmp_path, monkeypatch) -> None:
    ids = [f"GT-TEST-{index:03d}" for index in range(12)]
    question = "x" * 444
    _write_csv(
        tmp_path / "gt.csv",
        [
            {
                "id": record_id,
                "question": question,
                "split": "eval",
                "frozen_core": "false",
                "template_family": f"synthetic-budget-{index}",
            }
            for index, record_id in enumerate(ids)
        ],
    )
    _write_gold(tmp_path / "gold.jsonl", [_gold(record_id) for record_id in ids])
    _bind(monkeypatch, ids, tmp_path / "gt.csv", tmp_path / "gold.jsonl")
    history = [{"role": "user", "text": "L" * 1600}]
    messages = _messages_for(question, history)
    assert len(messages) == 26
    assert f"[user] {'L' * 1600}" in messages[-1]["content"]
    assert "L" * 1601 not in messages[-1]["content"]
    assert question in messages[1]["content"]
    assert estimate_input_tokens(messages) + 512 <= 8192


def test_prompt_has_definitions_no_record_text() -> None:
    text = Path("copilot/interpret/prompts/interp-v2.txt").read_text(encoding="utf-8")
    system = text.split("=== SYSTEM ===", 1)[1].split("=== FEWSHOT ===", 1)[0]
    definitions = system.split("Intent definitions", 1)[1]
    for intent in _INTENTS:
        assert f"- {intent}:" in definitions
    assert "GT-LBS-" not in text
