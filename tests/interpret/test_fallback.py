"""Acceptance test 5: rules-only fallback, abstain, and the call ceiling."""

from __future__ import annotations

from copilot.interpret import interpret
from copilot.interpret import seam
from copilot.llm.client import LLMError
from tests.support.helpers import ScriptedClient, completion, model_json


def _body(output) -> dict:
    body = output.model_dump(mode="json")
    errors = seam.validate_interpretation_output(body)
    assert errors == []
    return body


def test_timeout_abstains_with_a_schema_valid_output() -> None:
    client = ScriptedClient([LLMError("timeout")])
    body = _body(interpret({"query": "Need directions pricing"}, client=client))
    assert len(client.calls) == 1
    assert body["abstain"] is True
    assert body["abstain_reason"] == "model_unavailable"
    assert body["api"] == "directions"
    assert body["intent"] == "pricing_quote"
    assert body["team"] is None
    assert body["qualification_details"]["use_case_api"]["api"] == "directions"
    assert body["trace"]["model_calls"] == 1
    assert body["trace"]["repair_attempts"] == 0


def test_fallback_with_no_rule_match_nulls_intent_api_and_team() -> None:
    client = ScriptedClient([LLMError("timeout")])
    body = _body(interpret({"query": "hello there"}, client=client))
    assert body["abstain"] is True
    assert body["abstain_reason"] == "model_unavailable"
    assert body["intent"] is None
    assert body["api"] is None
    assert body["team"] is None
    assert body["qualification_details"]["use_case_api"] is None
    assert "use_case_api" in body["missing_required_fields"]


def test_fallback_with_one_api_keyword_keeps_only_that_api() -> None:
    client = ScriptedClient([LLMError("timeout")])
    body = _body(interpret({"query": "Need the distance matrix price"}, client=client))
    assert body["abstain"] is True
    assert body["abstain_reason"] == "model_unavailable"
    assert body["api"] == "distance_matrix"
    assert body["intent"] == "pricing_quote"
    assert body["team"] is None
    assert body["qualification_details"]["use_case_api"] == {
        "api": "distance_matrix",
        "use_case": None,
    }
    assert "use_case_api" not in body["missing_required_fields"]


def test_503_does_not_loop_inside_interpret() -> None:
    client = ScriptedClient([LLMError("unavailable", status_code=503)])
    body = _body(interpret({"query": "hello there"}, client=client))
    assert len(client.calls) == 1
    assert body["abstain"] is True
    assert body["abstain_reason"] == "model_unavailable"


def test_invalid_json_twice_stops_after_two_calls() -> None:
    client = ScriptedClient(
        [completion("this is not json"), completion("still not json at all")]
    )
    body = _body(interpret({"query": "hello there"}, client=client))
    assert len(client.calls) == 2
    assert client.calls[0]["temperature"] == 0
    assert client.calls[0]["max_tokens"] == 512
    assert body["abstain"] is True
    assert body["abstain_reason"] == "invalid_after_repair"
    assert body["intent"] is None
    assert body["api"] is None
    assert body["team"] is None
    assert body["trace"]["model_calls"] == 2
    assert body["trace"]["repair_attempts"] == 1


def test_budget_exceeded_does_not_count_as_a_model_call() -> None:
    from copilot.llm.client import BudgetExceeded

    client = ScriptedClient([BudgetExceeded("over budget")])
    body = _body(interpret({"query": "hello there"}, client=client))
    assert len(client.calls) == 1
    assert body["abstain_reason"] == "budget_exceeded"
    assert body["trace"]["model_calls"] == 0


def test_parseable_unresolved_labels_use_no_rule_match() -> None:
    unresolved = model_json(intent=None, sub_intent=None, api=None, team=None)
    client = ScriptedClient([completion(unresolved), completion(unresolved)])
    body = _body(interpret({"query": "hello there"}, client=client))
    assert len(client.calls) == 2
    assert body["abstain"] is True
    assert body["abstain_reason"] == "no_rule_match"
    assert body["intent"] is None
    assert body["api"] is None
    assert body["team"] is None
    assert body["qualification_details"]["use_case_api"] is None
    assert body["qualification_details"]["complete"] is False
    assert "use_case_api" in body["missing_required_fields"]


def test_one_repair_then_a_valid_reply() -> None:
    good = model_json()
    client = ScriptedClient([completion(model_json(intent="nope")), completion(good)])
    output = interpret({"query": "What parameters does the API take?"}, client=client)
    assert len(client.calls) == 2
    repair_user = client.calls[1]["messages"][-1]["content"]
    assert "/intent" in repair_user
    assert "ENUM" in repair_user
    body = _body(output)
    assert body["abstain"] is False
    assert "abstain_reason" in body
    assert body["abstain_reason"] is None
    assert body["intent"] == "docs_how_to"
    assert body["trace"]["repair_attempts"] == 1
    assert body["trace"]["model_calls"] == 2
