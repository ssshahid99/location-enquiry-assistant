"""Activity 17, end to end on 20 consequential actions (10 escalations + 10 sandbox record updates, created through the chat):
100% are held for review (nothing applied before a decision); every decision is logged with reviewer, timestamp, decision and the final diff;
approve / reject / edit all work; the review rules hold (authorised approver, a reason for every rejection, one decision per action)."""
import pytest

from lbs_rag.chat import chat as C
from lbs_rag.chat import registry, sandbox
from lbs_rag.store import cases
from lbs_rag.store.db import connect
from lbs_rag.store.handlers import approve_and_apply, reject

INCIDENTS = [
    "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.",
    "URGENT outage: every Ola Directions call returns 500 in production, our dispatch system is down.",
    "PRODUCTION: Ola Autocomplete is degraded, all requests return 5xx since 09:30 IST.",
    "A developer accidentally pushed our Ola Maps API key to a public GitHub repo. What should we do?",
    "Our API key was leaked and there is a bot attack on our usage and bill.",
    "Our prepaid Ola credits ran out and autocomplete stopped for paying users. Can you restore it now?",
    "We plan 80M Ola Distance Matrix pairs a month and want a committed-use price with an SLA.",
    "We need an SLA and data residency in India, and want to negotiate a custom plan.",
    "A user reports Ola reverse geocoding shows their home in the wrong pin code.",
    "I want to talk to a human agent please",
]
RECORDS = [(f"TKT-{50000 + i}" if i % 2 == 0 else f"OPP-{70000 + i}") for i in range(10)]
RECORD_MSG = "Sandbox CRM record {rid}. Customer wrote: \"How do we get alternative routes from Google Directions?\" Draft the reply and propose the field updates."


class _Docs(registry.Registry):
    """Real registry; only the documentation search is stubbed (no model)."""


def _reg():
    reg = registry.build()
    reg._tools["doc_search"] = (registry.DocSearchIn, registry.DocSearchOut, lambda a, ctx: {
        "text": "Use alternatives=true [1].", "citations": [{"n": 1, "chunk_id": "c", "doc_url": "https://example.test/doc", "provider": "Google Maps Platform",
                                                             "fetched_at": "2026-11-14"}], "retrieved": [], "tool_calls": [], "abstained": False,
        "refused": False, "reason": None})
    return reg


@pytest.fixture
def queue(tmp_path):
    con = connect(tmp_path / "q.db")
    cases.reload_approvers()
    for i, msg in enumerate(INCIDENTS):
        r = C.chat(con, msg, f"C-E{i}", reg=_reg(), persist=False, use_model=False)
        assert r["route"] == "escalation" and r["schema_errors"] == [], msg
    for i, rid in enumerate(RECORDS):
        r = C.chat(con, RECORD_MSG.format(rid=rid), f"C-R{i}", reg=_reg(), persist=False, use_model=False)
        assert r["outputs"]["proposed_record_update"]["applicable"] and r["schema_errors"] == []
    yield con
    con.close()


def test_all_20_consequential_actions_are_held_for_review(queue):
    con = queue
    pend = cases.list_pending(con)
    assert len(pend) == 20
    assert sorted({p["type"] for p in pend}) == ["escalation", "record_update"]
    assert all(p["status"] == "pending" and p["approver"] is None and p["applied_at"] is None for p in pend)
    # nothing was applied or written before a human decided
    assert con.execute("SELECT COUNT(*) FROM sandbox_records").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM cases WHERE status IN ('escalated','in_progress','reply_approved')").fetchone()[0] == 0
    assert not cases.decision_log(con)


def test_review_rules(queue):
    con = queue
    pend = cases.list_pending(con)
    a, b = pend[0]["id"], pend[1]["id"]
    with pytest.raises(ValueError, match="not an authorised approver"):
        approve_and_apply(con, a, approver="mallory")
    with pytest.raises(ValueError, match="reason is required"):
        reject(con, a, approver="deepshikha", note="")
    with pytest.raises(ValueError):
        approve_and_apply(con, a, approver="")
    approve_and_apply(con, a, approver="deepshikha")
    with pytest.raises(ValueError, match="already approved"):
        reject(con, a, approver="deepshikha", note="changed my mind")
    assert cases.get_action(con, b)["status"] == "pending"


def test_every_decision_is_logged_with_reviewer_timestamp_decision_and_final_diff(queue):
    con = queue
    pend = cases.list_pending(con)
    recs = [p for p in pend if p["type"] == "record_update"]
    escs = [p for p in pend if p["type"] == "escalation"]
    decided = {}
    for p in recs[:4]:                                   # approve as proposed
        approve_and_apply(con, p["id"], approver="deepshikha")
        decided[p["id"]] = ("approved", False)
    for p in recs[4:7]:                                  # approve with the approver's edit
        edited = {**p["payload"], "fields": {**p["payload"]["fields"], "status": "needs_followup", "next_step": "call customer"}}
        approve_and_apply(con, p["id"], approver="deepshikha", note="edited status", edited_payload=edited)
        decided[p["id"]] = ("approved", True)
    for p in recs[7:]:                                   # reject
        reject(con, p["id"], approver="deepshikha", note="wrong record")
        decided[p["id"]] = ("rejected", False)
    for p in escs[:5]:
        approve_and_apply(con, p["id"], approver="deepshikha")
        decided[p["id"]] = ("approved", False)
    for p in escs[5:8]:
        reject(con, p["id"], approver="deepshikha", note="handled by L1")
        decided[p["id"]] = ("rejected", False)
    log = {e["action_id"]: e for e in cases.decision_log(con)}
    assert set(log) == set(decided) and len(log) == 18       # two escalations still pending
    for aid, (decision, edited) in decided.items():
        e = log[aid]
        assert e["reviewer"] == "deepshikha" and e["decided_at"] and e["decision"] == decision and e["edited"] == edited and e["final"]
        if decision == "rejected":
            assert e["note"] and e["applied_at"] is None and e["final_diff"] is None
    # the final diff of an approved record update is what reached the sandbox, and the approver's edit wins
    for p in recs[:4]:
        e = log[p["id"]]
        assert e["final_diff"] == p["payload"]["diff"] and e["applied_at"]
        assert sandbox.get(con, p["payload"]["record_id"]) == p["payload"]["fields"]
    for p in recs[4:7]:
        e = log[p["id"]]
        assert e["final_diff"]["status"]["new"] == "needs_followup" and e["final_diff"]["next_step"]["new"] == "call customer"
        assert sandbox.get(con, p["payload"]["record_id"])["status"] == "needs_followup"
    for p in recs[7:]:
        assert sandbox.get(con, p["payload"]["record_id"]) == {}          # rejected: nothing written
    assert len(cases.list_pending(con)) == 2
    # approved escalations are applied internally only: the case is marked escalated and nothing is sent
    for p in escs[:5]:
        assert con.execute("SELECT status FROM cases WHERE id=?", (p["case_id"],)).fetchone()[0] == "escalated"


def test_a_non_sandbox_target_cannot_be_approved_into_existence(queue):
    con = queue
    aid = cases.request_approval(con, None, "record_update", {"record_id": "CUSTOMER-1", "mode": "propose", "fields": {"status": "x"}, "diff": {}})
    approve_and_apply_raises = False
    try:
        approve_and_apply(con, aid, approver="deepshikha")
    except sandbox.SandboxError:
        approve_and_apply_raises = True
    assert approve_and_apply_raises
    assert con.execute("SELECT COUNT(*) FROM sandbox_records").fetchone()[0] == 0
