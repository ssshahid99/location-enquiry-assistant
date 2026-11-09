import pytest

from lbs_rag.agent import orchestrator as O
from lbs_rag.store import cases, handlers
from lbs_rag.store.db import connect

CITE = [{"n": 1, "provider": "Ola Maps", "doc_url": "https://maps.olakrutrim.com/docs", "chunk_id": "none", "fetched_at": "x"}]


@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "o.db")


@pytest.fixture()
def fake(monkeypatch):
    """Stub the RAG everywhere it is imported, and record how often it was called (the cost of each route)."""
    calls = []

    def fake_answer(q, **k):
        calls.append(q)
        if "capital of France" in q or "ignore" in q.lower():
            return {"text": "I can't help with this request: it is outside what this system is designed to process.", "citations": [],
                    "abstained": True, "refused": True, "escalate": False, "reason": "refused:off_topic", "trace_id": "t", "tool_calls": []}
        return {"text": "Ola rate limiting returns a 429 response [1].", "citations": CITE, "abstained": False, "refused": False,
                "escalate": False, "reason": None, "trace_id": "t", "tool_calls": []}
    import lbs_rag.generate.answer as A
    monkeypatch.setattr(A, "answer", fake_answer)
    monkeypatch.setattr(O, "answer", fake_answer)
    return calls


def test_simple_question_goes_to_the_rag_without_opening_a_case(con, fake):
    r = O.handle_message(con, "What would Ola Maps Directions cost for 300,000 requests a month?", submit=False)
    assert r["route"] == "rag" and r["text"] and cases.list_cases(con) == [] and len(fake) == 1


def test_off_topic_is_refused_and_never_opens_a_case(con, fake):
    r = O.handle_message(con, "What is the capital of France?", submit=False)
    assert r["route"] == "refused" and cases.list_cases(con) == []


def test_support_incident_runs_the_full_agent_chain_and_everything_waits_for_approval(con, fake):
    msg = "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST."
    r = O.handle_message(con, msg, customer="Acme")
    assert r["route"] == "agent" and r["workflow"] == "support" and r["category"] == "outage" and r["urgency"] == "high"
    assert r["draft"] and r["critic"]["verdict"] in ("pass", "revise", "needs_human")
    types = sorted(a["type"] for a in r["approvals"])
    assert {"escalation", "owner_action", "external_send"} <= set(types)
    case = cases.get_case(con, r["case_id"])
    assert case["customer"] == "Acme" and case["type"] == "incident" and case["owner"] is None          # nothing applied
    assert cases.get_tracker(con, r["case_id"]) == {} and "nothing has been sent" in r["next_step"].lower() or "waiting" in r["next_step"].lower()
    assert len(fake) == 1                                                                              # one RAG call: the agent chain adds no model calls


def test_sales_discovery_gets_questions_capabilities_and_a_draft(con, fake):
    r = O.handle_message(con, "We're a Pune field-service app and need walking directions and to optimise the order of 20 stops. What do you recommend?")
    assert r["route"] == "agent" and r["workflow"] == "sales" and r["questions"]
    assert {"walking_cycling", "route_optimization"} <= {c["id"] for c in r["capabilities"]} and "[GAP]" in r["draft"]


def test_contract_request_goes_to_a_person_with_a_handoff_and_no_customer_draft(con, fake):
    r = O.handle_message(con, "We want Ola autocomplete for 50M requests a month with an SLA and India-only data residency.")
    assert r["route"] == "human" and r["draft"] is None and r["handoff"]["approval_id"]
    assert any(a["type"] == "handoff" for a in r["approvals"]) and fake == []                          # no model call at all
    assert cases.get_case(con, r["case_id"])["status"] == "needs_human"


def test_reply_resumes_the_case_and_never_reasks_or_duplicates(con, fake):
    r1 = O.handle_message(con, "Ola Directions returns 429 for most calls")
    assert any(q["key"] == "timestamp" for q in r1["questions"])
    n1 = len(cases.list_pending(con, r1["case_id"]))
    r2 = O.reply(con, r1["case_id"], "It started at 14:05 IST today and is still happening")
    assert r2["case_id"] == r1["case_id"] and r2["route"] == "agent"
    assert all(q["key"] != "timestamp" for q in r2["questions"])
    assert len(cases.list_pending(con, r1["case_id"])) == n1                                           # refreshed, not duplicated
    assert "executor" in cases.load_state(con, r1["case_id"]) and "critic" in cases.load_state(con, r1["case_id"])   # state survived the replan


def test_pii_is_removed_before_anything_is_stored(con, fake):
    r = O.handle_message(con, "Ola Directions returns 429 for most calls. Contact me at john.doe@example.com or +91 98765 43210")
    assert "EMAIL" in r["redacted"] and "PHONE" in r["redacted"]
    stored = " ".join(m["content"] for m in cases.get_case_history(con, r["case_id"]))
    assert "john.doe@example.com" not in stored and "98765" not in stored


def test_full_approval_cycle_updates_only_after_the_approver_acts(con, fake):
    r = O.handle_message(con, "Ola Directions returns 429 for 80% of calls since 14:05 IST today", customer="Acme")
    cid = r["case_id"]
    by_type = {a["type"]: a["id"] for a in r["approvals"]}
    assert cases.get_case(con, cid)["status"] == "awaiting_approval"
    handlers.approve_and_apply(con, by_type["tracker_update"], approver="deepshikha")
    assert cases.get_tracker(con, cid)["category"] == "rate_limit"
    handlers.approve_and_apply(con, by_type["owner_action"], approver="deepshikha")
    assert cases.get_case(con, cid)["next_action"]
    handlers.approve_and_apply(con, by_type["external_send"], approver="deepshikha", note="ok")
    assert cases.get_case(con, cid)["status"] == "reply_approved"
    assert "nothing was sent" in cases.get_action(con, by_type["external_send"])["applied_result"].lower()
    events = [e["event"] for e in cases.case_timeline(con, cid)]
    assert events.count("action_applied") == 3


def test_rejection_reason_lands_in_the_history_for_the_next_pass(con, fake):
    r = O.handle_message(con, "Ola Directions returns 429 for 80% of calls since 14:05 IST today")
    send = [a for a in r["approvals"] if a["type"] == "external_send"][0]["id"]
    handlers.reject(con, send, approver="deepshikha", note="too long; ask for request ids first")
    hist = " ".join(m["content"] for m in cases.get_case_history(con, r["case_id"]))
    assert "[rejected external_send] too long; ask for request ids first" in hist


def test_prepare_handoff_is_idempotent_and_reports_gaps(con, fake):
    r = O.handle_message(con, "Ola Directions returns 429 for most calls")
    h1 = O.prepare_handoff(con, r["case_id"])
    h2 = O.prepare_handoff(con, r["case_id"])
    assert h1["approval_id"] == h2["approval_id"] and "Handoff for case" in h1["text"]
    done = handlers.approve_and_apply(con, h1["approval_id"], approver="deepshikha", edited_payload={**cases.get_action(con, h1["approval_id"])["payload"], "assign_to": "asha"})
    assert cases.get_case(con, r["case_id"])["status"] == "handed_off" and cases.get_case(con, r["case_id"])["owner"] == "asha"
    assert done["applied_at"]


def test_unknown_case_is_an_error(con, fake):
    with pytest.raises(ValueError):
        O.reply(con, "does-not-exist", "hello")
