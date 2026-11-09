import pytest
from fastapi.testclient import TestClient

from lbs_rag.agent import orchestrator as O
from lbs_rag.api import server
from lbs_rag.store import cases, handlers
from lbs_rag.store import repositories as repo
from lbs_rag.store.db import connect


@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "c.db")


def _case_with_pending(con):
    cid = cases.create_case(con, workflow="support")
    cases.add_message(con, cid, "customer", "Ola Directions returns 429")
    a1 = cases.request_approval(con, cid, "external_send", {"draft": "x"})
    a2 = cases.request_approval(con, cid, "tracker_update", {"changes": {"category": {"old": None, "new": "rate_limit"}}})
    return cid, a1, a2


def test_closing_needs_an_authorised_human_and_a_note(con):
    cid, _, _ = _case_with_pending(con)
    for who in ("agent", "", "someone else"):
        with pytest.raises(ValueError):
            cases.close_case(con, cid, approver=who, note="done")
    with pytest.raises(ValueError, match="resolution note"):
        cases.close_case(con, cid, approver="deepshikha", note="  ")
    assert cases.get_case(con, cid)["status"] != "closed"


def test_close_cancels_pending_requests_and_applies_nothing(con):
    cid, a1, a2 = _case_with_pending(con)
    closed = cases.close_case(con, cid, approver="Deepshikha", note="resolved with the customer")
    assert closed["status"] == "closed" and closed["next_action"].startswith("Closed:")
    assert cases.list_pending(con, cid) == []
    assert cases.get_action(con, a1)["status"] == "cancelled" and cases.get_action(con, a2)["status"] == "cancelled"
    assert cases.get_tracker(con, cid) == {}                                   # the cancelled tracker update was never applied
    with pytest.raises(ValueError):
        handlers.approve_and_apply(con, a1, approver="deepshikha")             # a cancelled request cannot be approved later
    events = [e["event"] for e in cases.case_timeline(con, cid)]
    assert "case_closed" in events and any("[case closed]" in m["content"] for m in cases.get_case_history(con, cid))
    with pytest.raises(ValueError, match="already closed"):
        cases.close_case(con, cid, approver="deepshikha", note="again")


def test_reopen_rules_and_cancelled_requests_stay_cancelled(con):
    cid, a1, _ = _case_with_pending(con)
    with pytest.raises(ValueError, match="not closed"):
        cases.reopen_case(con, cid, approver="deepshikha", note="x")
    cases.close_case(con, cid, approver="deepshikha", note="done")
    with pytest.raises(ValueError):
        cases.reopen_case(con, cid, approver="agent", note="x")
    with pytest.raises(ValueError, match="reason is required"):
        cases.reopen_case(con, cid, approver="deepshikha", note="")
    assert cases.reopen_case(con, cid, approver="deepshikha", note="customer wrote back")["status"] == "open"
    assert cases.get_action(con, a1)["status"] == "cancelled"


def test_a_closed_case_does_not_silently_take_new_messages(con, monkeypatch):
    import lbs_rag.generate.answer as A
    fake = lambda q, **k: {"text": "x [1]", "citations": [], "abstained": False, "refused": False, "escalate": False, "reason": None, "trace_id": "t", "tool_calls": []}
    monkeypatch.setattr(A, "answer", fake)
    monkeypatch.setattr(O, "answer", fake)
    r = O.handle_message(con, "Ola Directions returns 429 for most calls")
    cases.close_case(con, r["case_id"], approver="deepshikha", note="done")
    with pytest.raises(ValueError, match="closed"):
        O.reply(con, r["case_id"], "any update?")
    cases.reopen_case(con, r["case_id"], approver="deepshikha", note="customer wrote back")
    assert O.reply(con, r["case_id"], "It started at 14:05 IST today")["case_id"] == r["case_id"]


def test_api_close_and_reopen(tmp_path, monkeypatch):
    db = tmp_path / "api.db"
    monkeypatch.setattr(repo, "connect", lambda: connect(db))
    c = connect(db)
    cid = cases.create_case(c, workflow="support")
    cases.request_approval(c, cid, "external_send", {"draft": "x"})
    client = TestClient(server.app)
    assert client.post(f"/cases/{cid}/close", json={"approver": "agent", "note": "x"}).status_code == 409
    assert client.post(f"/cases/{cid}/close", json={"approver": "deepshikha", "note": ""}).status_code == 409
    assert client.post("/cases/nope/close", json={"approver": "deepshikha", "note": "x"}).status_code == 404
    ok = client.post(f"/cases/{cid}/close", json={"approver": "deepshikha", "note": "resolved"})
    assert ok.status_code == 200 and ok.json()["status"] == "closed" and client.get("/actions/pending").json() == []
    assert client.post(f"/cases/{cid}/reply", json={"message": "hello again"}).status_code == 409
    assert client.post(f"/cases/{cid}/reopen", json={"approver": "deepshikha", "note": "customer wrote back"}).json()["status"] == "open"
