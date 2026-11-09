import json
from types import SimpleNamespace

import pytest

from lbs_rag.agent import planner, planner_fallback as fb, router


def fake_client(reply, calls=None):
    def create(**kw):
        if calls is not None:
            calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


GOOD = json.dumps({"workflow": "support", "category": "troubleshooting", "confidence": "high"})
UNCLEAR_MSG = "Hmm, ours has been behaving oddly since Tuesday."


def test_parse_accepts_only_valid_confident_labels():
    assert fb.parse_label(GOOD) == {"workflow": "support", "category": "troubleshooting", "confidence": "high"}
    assert fb.parse_label("<think>hmm</think>" + GOOD)["workflow"] == "support"
    assert fb.parse_label("Sure! " + GOOD + " hope that helps")["category"] == "troubleshooting"
    for bad in ["", "no json here", "{not json}",
                json.dumps({"workflow": "support", "category": "troubleshooting", "confidence": "low"}),       # not confident
                json.dumps({"workflow": "unclear", "category": None, "confidence": "high"}),                  # nothing to apply
                json.dumps({"workflow": "support", "category": "pricing", "confidence": "high"}),             # category of the other workflow
                json.dumps({"workflow": "billing", "category": "billing", "confidence": "high"}),             # invented workflow
                json.dumps({"workflow": "sales", "category": "refund_everything", "confidence": "high"})]:    # invented category
        assert fb.parse_label(bad) is None


def test_failures_never_raise_and_leave_the_rules_answer():
    def boom(**kw):
        raise RuntimeError("gateway down")
    c = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    assert fb.suggest("anything", client=c) is None


def test_fallback_is_off_by_default_and_rules_stay_in_charge():
    assert planner.config()["model_fallback"]["enabled"] is False
    assert planner.plan(UNCLEAR_MSG)["workflow"] == "unclear"                        # no model, no change


def test_fallback_fills_only_an_unclear_classification():
    calls = []
    p = planner.plan(UNCLEAR_MSG, model_fallback=True, fallback_client=fake_client(GOOD, calls))
    assert p["workflow"] == "support" and p["category"] == "troubleshooting" and len(calls) == 1
    assert any(s["step"] == "model_fallback" for s in p["steps"])
    assert p["signals"]["source"] == "model" if "signals" in p else True


def test_rule_classified_messages_never_call_the_model():
    calls = []
    p = planner.plan("PRODUCTION: Ola Distance Matrix has returned 5xx for all requests", model_fallback=True,
                     fallback_client=fake_client(GOOD, calls))
    assert calls == [] and p["category"] == "outage"


def test_invalid_model_label_leaves_the_message_unclear():
    p = planner.plan(UNCLEAR_MSG, model_fallback=True, fallback_client=fake_client("I think it is a billing thing"))
    assert p["workflow"] == "unclear"


def test_the_model_cannot_trigger_escalation_or_facts():
    plain = planner.plan(UNCLEAR_MSG, model_fallback=False)
    with_fb = planner.plan(UNCLEAR_MSG, model_fallback=True, fallback_client=fake_client(
        json.dumps({"workflow": "support", "category": "security", "confidence": "high"})))
    assert with_fb["escalation"]["recommend"] == plain["escalation"]["recommend"] is False   # escalation is rules only
    assert with_fb["known_facts"] == plain["known_facts"]                                    # facts are rules only


def test_message_sent_to_the_model_is_capped_and_only_contains_the_message():
    calls = []
    fb.suggest("x" * 5000, client=fake_client(GOOD, calls))
    sent = calls[0]["messages"]
    assert len(sent[1]["content"]) == 1500 and sent[0]["role"] == "system" and len(sent) == 2
