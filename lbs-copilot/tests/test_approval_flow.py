"""End to end: an escalation opens a case + one pending approval; ONE authorised human approves; the internal update applies once."""
import pytest

from lbs_rag.generate import answer as A
from lbs_rag.store import cases, handlers
from lbs_rag.store import repositories as repo
from lbs_rag.store.db import connect


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "flow.db"
    monkeypatch.setattr(A.repo, "connect", lambda: connect(path))
    return path


class _Fake:
    def __init__(self, text):
        self.chat = type("C", (), {"completions": type("K", (), {"create": lambda s, **k: type("R", (), {"choices": [
            type("Ch", (), {"message": type("M", (), {"content": text})()})()]})()})()})()


def test_escalation_opens_case_and_pending_approval_then_approve_applies_once(db, monkeypatch):
    monkeypatch.setattr(A, "openai_client", lambda: _Fake("This is being handed to Ola support. Please collect request ids."))
    monkeypatch.setattr(A, "retrieve", lambda *a, **k: [])
    monkeypatch.setattr(A.pricing_intent, "extract", lambda q: {"pricing": False, "items": []})
    out = A.answer("PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.", persist=True, rewrite=False)
    assert out["escalate"] and out["case_id"] and out["proposed_action_id"]

    con = connect(db)
    case = cases.get_case(con, out["case_id"])
    assert case["type"] == "incident" and case["category"] == "production_incident" and case["urgency"] == "high"
    assert case["status"] == "awaiting_approval"
    assert [m["role"] for m in cases.get_case_history(con, out["case_id"])] == ["customer", "agent"]
    assert [a["id"] for a in cases.list_pending(con)] == [out["proposed_action_id"]]

    with pytest.raises(ValueError):                                   # nobody else, and never the agent
        handlers.approve_and_apply(con, out["proposed_action_id"], approver="agent")
    done = handlers.approve_and_apply(con, out["proposed_action_id"], approver="deepshikha", note="confirmed outage",
                                      edited_payload={"draft_reply": "Edited by approver"})
    assert done["applied_at"] and done["status"] == "approved"
    case = cases.get_case(con, out["case_id"])
    assert case["status"] == "escalated" and case["owner"] == "deepshikha" and "Hand off to" in case["next_action"]
    with pytest.raises(ValueError):
        handlers.approve_and_apply(con, out["proposed_action_id"], approver="deepshikha")     # a decision is final; no double apply
    events = [e["event"] for e in cases.case_timeline(con, out["case_id"])]
    assert events.count("action_applied") == 1 and "approval_approved" in events


def test_reject_needs_reason_and_closes_the_loop(db):
    con = connect(db)
    cid = cases.create_case(con, type="incident")
    aid = repo.create_proposed_action(con, type="escalation", case_id=cid, payload={"owner": "Ola support", "collect": []})
    with pytest.raises(ValueError, match="reason is required"):
        handlers.reject(con, aid, approver="deepshikha", note="")
    handlers.reject(con, aid, approver="deepshikha", note="not an outage; customer misread the error")
    assert cases.get_case(con, cid)["status"] == "rejected"
    with pytest.raises(ValueError):
        handlers.approve_and_apply(con, aid, approver="deepshikha")


def test_external_send_is_recorded_never_sent(db):
    con = connect(db)
    aid = cases.request_approval(con, None, "external_send", {"to": "customer", "draft": "Hello"})
    done = handlers.approve_and_apply(con, aid, approver="deepshikha")
    assert "nothing was sent externally" in done["applied_result"]
