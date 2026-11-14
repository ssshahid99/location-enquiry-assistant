"""The /chat endpoint and its review endpoints, through FastAPI's test client (no model: documentation search is stubbed)."""
import pytest
from fastapi.testclient import TestClient

from lbs_rag.api import server
from lbs_rag.chat import registry
from lbs_rag.store import cases
from lbs_rag.store.db import connect


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "api.db"
    monkeypatch.setattr(server, "_db", lambda: connect(db))
    real_build = registry.build

    def fake_build():
        reg = real_build()
        reg._tools["doc_search"] = (registry.DocSearchIn, registry.DocSearchOut, lambda a, ctx: {
            "text": "Ola Directions includes 100,000 free requests a month [1].",
            "citations": [{"n": 1, "chunk_id": "c1", "doc_url": "https://maps.olakrutrim.com/pricing/details", "provider": "Ola Maps",
                           "fetched_at": "2026-11-14"}], "retrieved": [], "tool_calls": [], "abstained": False, "refused": False, "reason": None})
        return reg
    monkeypatch.setattr(registry, "build", fake_build)
    from lbs_rag.chat import chat as chat_mod
    monkeypatch.setattr(chat_mod.settings, "chat_model", "stub", raising=False)
    cases.reload_approvers()
    return TestClient(server.app)


def test_chat_returns_route_text_and_seven_valid_outputs(client):
    r = client.post("/chat", json={"message": "What is the free allowance for Ola Maps Directions?", "customer": "t"})
    assert r.status_code == 200
    j = r.json()
    assert j["route"] == "rag" and j["schema_errors"] == [] and "100,000" in j["text"] and j["conv_id"]
    assert set(j["outputs"]) == {"response_draft", "clarifying_questions", "qualification_details", "proposed_record_update", "comparison",
                                 "responsible_role", "next_action"}


def test_conversation_state_is_saved_and_readable(client):
    cid = client.post("/chat", json={"message": "We need Ola Directions for about 500,000 requests a month"}).json()["conv_id"]
    s = client.get(f"/chat/{cid}")
    assert s.status_code == 200 and s.json()["facts"].get("volume") and len(s.json()["history"]) == 2
    assert client.get("/chat/does-not-exist").status_code == 404


def test_escalation_is_pending_until_a_person_decides_and_is_then_logged(client):
    j = client.post("/chat", json={"message": "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST."}).json()
    assert j["route"] == "escalation" and j["outputs"]["next_action"]["approval_status"] == "pending"
    pend = client.get("/actions/pending").json()
    assert len(pend) == 1 and pend[0]["status"] == "pending"
    assert client.get("/actions/log").json() == []
    d = client.post(f"/actions/{pend[0]['id']}/decide", json={"approve": True, "approver": "deepshikha"})
    assert d.status_code == 200
    log = client.get("/actions/log").json()
    assert len(log) == 1 and log[0]["reviewer"] == "deepshikha" and log[0]["decision"] == "approved" and log[0]["decided_at"]
    assert client.post(f"/actions/{pend[0]['id']}/decide", json={"approve": False, "approver": "deepshikha", "note": "x"}).status_code == 409


def test_record_update_reaches_the_sandbox_only_after_approval(client):
    j = client.post("/chat", json={"message": "Sandbox CRM ticket TKT-54863. Customer wrote: \"How do we get alternative routes?\" "
                                             "Draft the reply and propose the ticket field updates."}).json()
    assert j["outputs"]["proposed_record_update"]["applicable"] and j["record_action_id"]
    assert client.get("/sandbox/records/TKT-54863").json()["fields"] == {}
    client.post(f"/actions/{j['record_action_id']}/decide", json={"approve": True, "approver": "deepshikha"})
    assert client.get("/sandbox/records/TKT-54863").json()["fields"]["status"] == "solution_proposed"
    assert client.get("/sandbox/records/CUSTOMER-9").status_code == 404


def test_bad_requests_are_rejected(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "hi", "conversation_id": "../x"}).status_code == 422
    assert client.post("/actions/999/decide", json={"approve": True, "approver": "mallory"}).status_code == 409
