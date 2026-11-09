"""API smoke tests with the RAG stubbed and a temporary database (no gateway, no real data touched)."""
import pytest
from fastapi.testclient import TestClient

from lbs_rag.agent import orchestrator as O
from lbs_rag.api import server
from lbs_rag.store import repositories as repo
from lbs_rag.store.db import connect


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "api.db"
    monkeypatch.setattr(repo, "connect", lambda: connect(db))
    import lbs_rag.generate.answer as A

    def fake(q, **k):
        return {"text": "Ola rate limiting returns a 429 response [1].", "citations": [], "abstained": False, "refused": False,
                "escalate": False, "reason": None, "trace_id": "t", "tool_calls": []}
    monkeypatch.setattr(A, "answer", fake)
    monkeypatch.setattr(O, "answer", fake)
    monkeypatch.setattr(server, "answer", fake)
    return TestClient(server.app)


def test_health_and_approvers(client):
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/approvers").json()["required_approvals"] == 1


def test_simple_question_is_answered_without_a_case(client):
    r = client.post("/cases/message", json={"message": "What would Ola Maps Directions cost for 300,000 requests a month?"}).json()
    assert r["route"] == "rag" and client.get("/cases").json() == []


def test_incident_case_lifecycle_through_the_api(client):
    r = client.post("/cases/message", json={"message": "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.",
                                            "customer": "Acme"}).json()
    cid = r["case_id"]
    assert r["route"] == "agent" and r["critic"]["verdict"] in ("pass", "revise", "needs_human") and len(r["approvals"]) >= 3
    pend = client.get("/actions/pending").json()
    assert all(p["status"] == "pending" for p in pend)
    send = next(p for p in pend if p["type"] == "external_send")
    bad = client.post(f"/actions/{send['id']}/decide", json={"approve": True, "approver": "agent"})
    assert bad.status_code == 409                                                     # only a listed human may approve
    no_reason = client.post(f"/actions/{send['id']}/decide", json={"approve": False, "approver": "deepshikha"})
    assert no_reason.status_code == 409                                               # rejection needs a reason
    ok = client.post(f"/actions/{send['id']}/decide", json={"approve": True, "approver": "deepshikha"}).json()
    assert ok["status"] == "approved" and "nothing was sent externally" in ok["note"]
    detail = client.get(f"/cases/{cid}").json()
    assert detail["case"]["status"] == "reply_approved" and detail["history"] and "timeline" in detail
    rep = client.post(f"/cases/{cid}/reply", json={"message": "It started at 14:05 IST today"}).json()
    assert rep["case_id"] == cid and rep["route"] == "agent"
    ho = client.post(f"/cases/{cid}/handoff").json()
    assert ho["approval_id"] and "Handoff for case" in ho["text"]


def test_unknown_case_is_404(client):
    assert client.get("/cases/nope").status_code == 404
    assert client.post("/cases/nope/reply", json={"message": "hello there"}).status_code == 404
    assert client.post("/cases/nope/handoff").status_code == 404
