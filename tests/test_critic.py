import pytest

from lbs_rag.agent import critic as C
from lbs_rag.agent import executor as E
from lbs_rag.agent import planner as P
from lbs_rag.store import cases
from lbs_rag.store.db import connect
from lbs_rag.tools.capabilities import match_capabilities

CHUNKS = {"c1": "Ola Directions allows up to 25 waypoints per request. Distance Matrix allows a maximum of 50 pairs per request. "
                "Rate limiting returns a 429 response when you exceed the limit."}
CITE = [{"n": 1, "provider": "Ola Maps", "doc_url": "https://maps.olakrutrim.com/docs", "chunk_id": "c1"}]
PLAN = {"questions": [{"key": "timestamp", "question": "When did this start?"}], "needs_human_followup": [], "known_facts": {}, "missing_required": []}


def make(text, *, validations=None, caps=None, cites=CITE, kind="support_reply", plan=PLAN, tool_calls=None, draft=None):
    k = {"text": text, "citations": cites, "tool_calls": tool_calls or []}
    d = draft or E.draft_communication(kind, plan=plan, knowledge=k, validations=validations, capability_matches=caps)
    return {"kind": kind, "draft": d, "knowledge": k, "validations": validations or [], "capabilities": caps or []}


def issues(r, check):
    return [i for i in r["issues"] if i["check"] == check]


# ------------------------------------------------------------------ individual checks
def test_clean_grounded_draft_passes():
    r = C.review(make("Ola Directions allows up to 25 waypoints per request [1]."), PLAN, chunks=CHUNKS)
    assert r["verdict"] == "pass" and r["issues"] == []


def test_invented_number_is_flagged_by_grounding_and_numbers():
    r = C.review(make("Ola Directions allows up to 99 waypoints per request [1]."), PLAN, chunks=CHUNKS)
    assert issues(r, "grounding") and issues(r, "numbers") and r["verdict"] == "revise"


def test_commitments_are_flagged_but_a_negated_disclaimer_is_not():
    r = C.review(make("We guarantee a fix within 4 hours. Ola allows up to 25 waypoints per request [1]."), PLAN, chunks=CHUNKS)
    assert issues(r, "commitments")
    ok = make("Ola allows up to 25 waypoints per request [1].", plan={**PLAN, "escalation": {"recommend": True, "owner": "Ola support"}})
    assert "not promising any SLA" in ok["draft"] and not issues(C.review(ok, PLAN, chunks=CHUNKS), "commitments")


def test_secrets_and_personal_data_force_human_review():
    r = C.review(make("Please contact john.doe@example.com. Ola allows up to 25 waypoints per request [1]."), PLAN, chunks=CHUNKS)
    assert issues(r, "safety") and r["verdict"] == "needs_human"
    public = make("Contact maps-support@olakrutrim.com. Ola allows up to 25 waypoints per request [1].")
    assert not issues(C.review(public, PLAN, chunks=CHUNKS), "safety")


def test_omitted_failed_validation_is_flagged():
    val = [{"check": "timestamp", "ok": False, "detail": "time '14:05' has no timezone: ask for date and timezone"}]
    ex = make("Ola allows up to 25 waypoints per request [1].", draft="Hello, here is our answer. DRAFT: not sent.")
    ex["validations"] = val
    assert issues(C.review(ex, PLAN, chunks=CHUNKS), "validations")


def test_gap_written_up_as_unsupported_is_flagged_and_documented_without_source_is_flagged():
    caps = match_capabilities("optimise the order of stops", ["Ola Maps", "HERE"])
    ex = make("Ola allows up to 25 waypoints per request [1].", caps=caps)
    ex["draft"] += "\nHERE does not support route optimisation."
    assert issues(C.review(ex, PLAN, chunks=CHUNKS), "capability")
    fake = [{"id": "x", "label": "X", "gaps": [], "providers": [{"provider": "Ola Maps", "documented": True, "url": None, "chunk_id": None,
                                                              "statement": "s", "quote": "q"}]}]
    assert issues(C.review(make("Ola allows up to 25 waypoints per request [1].", caps=fake), PLAN, chunks=CHUNKS), "capability")


def test_calculator_and_price_summary_numbers_are_allowed():
    text = ("Ola Maps Reverse Geocode costs less.\n\nPrice summary (calculated from the verified list prices; FX 94.1402 INR/USD, 2026-11-06):\n"
            "- Ola Maps | Reverse Geocode: INR 860,100.00\nList-price sources: [1].")
    ex = make(text, tool_calls=[{"output": [{"cost": "860100.00", "cost_inr": "860100.00", "units": 20000000}]}])
    assert not issues(C.review(ex, PLAN, chunks=CHUNKS), "numbers")


# ------------------------------------------------------------------ handoff completeness
def pkg(**kw):
    base = {"case_id": "c", "customer": "x", "workflow": "support", "category": "outage", "urgency": "high", "provider": "Ola Maps", "api": None,
            "owner": None, "status": "open", "requirements": {}, "findings": {"known_facts": {}, "validations": [], "capabilities": [],
                                                                            "knowledge_answer": None, "knowledge_sources": []},
            "attempts": [], "open_questions": [], "needs_human_followup": [], "next_action": None, "pending_approvals": [], "messages": 1}
    base.update(kw)
    return base


def test_handoff_missing_pieces_are_reported():
    kinds = " | ".join(i["detail"] for i in C.check_handoff(pkg(), {"missing_required": ["timestamp"]}))
    for needle in ("no findings", "no owner", "no next action", "'timestamp'"):
        assert needle in kinds


def test_handoff_ok_when_owner_is_proposed_and_gaps_are_listed():
    p = pkg(findings={"known_facts": {}, "validations": [], "capabilities": [], "knowledge_answer": "x", "knowledge_sources": []},
            open_questions=["timestamp"])
    assert C.check_handoff(p, {"missing_required": ["timestamp"]}, pending_types=["owner_action"]) == []


def test_sales_handoff_requires_core_requirements_or_explicit_unresolved():
    p = pkg(workflow="sales", owner="a", next_action="n", findings={"known_facts": {}, "validations": [], "capabilities": [{"id": "x"}],
                                                                   "knowledge_answer": None, "knowledge_sources": []},
            requirements={"use_case": "delivery"}, open_questions=["volume"])
    d = [i["detail"] for i in C.check_handoff(p, {"missing_required": []})]
    assert any("api_needs" in x for x in d) and not any("volume" in x for x in d) and not any("use_case" in x for x in d)


# ------------------------------------------------------------------ revision loop
def test_revision_drops_unsupported_sentences_and_then_passes():
    ex = make("Ola allows up to 25 waypoints per request [1]. Ola also allows 99 waypoints per request [1]. We guarantee a fix within 4 hours.")
    out = C.critique_and_revise(ex, PLAN, chunks=CHUNKS)
    assert out["verdict"] == "pass" and out["revisions"] == 1
    d = out["result"]["draft"]
    assert "25 waypoints" in d and "99" not in d and "guarantee" not in d
    assert [h["verdict"] for h in out["history"]] == ["revise", "pass"]


def test_unfixable_issue_ends_in_needs_human_without_pointless_revisions():
    out = C.critique_and_revise(make("Contact john.doe@example.com [1]."), PLAN, chunks=CHUNKS)
    assert out["verdict"] == "needs_human" and out["revisions"] == 0


def test_persistent_issue_stops_after_max_revisions():
    bad = [{"id": "x", "label": "X", "gaps": [], "providers": [{"provider": "Ola Maps", "documented": True, "url": None, "chunk_id": None,
                                                              "statement": "s", "quote": "q"}]}]
    out = C.critique_and_revise(make("Ola allows up to 25 waypoints per request [1].", caps=bad), PLAN, chunks=CHUNKS)
    assert out["verdict"] == "needs_human" and out["revisions"] == C.MAX_REVISIONS


# ------------------------------------------------------------------ persisted case
@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "c.db")


def _fake_rag(monkeypatch, text):
    import lbs_rag.generate.answer as A
    monkeypatch.setattr(A, "answer", lambda q, **k: {"text": text, "citations": CITE, "abstained": False, "refused": False,
                                                       "escalate": False, "reason": None, "tool_calls": []})


def test_critique_case_updates_the_pending_draft_and_records_the_verdict(con, monkeypatch):
    _fake_rag(monkeypatch, "Ola rate limiting returns a 429 response [1]. Ola will be fixed within 2 hours.")
    cid = cases.create_case(con, workflow="support")
    msg = "Ola Directions returns 429 for 80% of calls since 14:05 IST"
    pl = P.plan_case(con, cid, msg, submit=False)
    res = E.run_executor(con, cid, pl, msg)
    out = C.critique_case(con, cid, res, pl, chunks=CHUNKS)
    assert out["verdict"] == "pass" and out["revisions"] == 1
    assert out["handoff_ready"] is False and any("no owner" in i["detail"] for i in out["handoff_issues"])   # separate question from the draft
    pending = [a for a in cases.list_pending(con, cid) if a["type"] == "external_send"][0]
    assert "fixed within 2 hours" not in pending["payload"]["draft"] and pending["payload"]["critic"]["verdict"] == "pass"
    assert pending["payload"]["critic"]["handoff_ready"] is False
    st = cases.load_state(con, cid)
    assert st["critic"]["verdict"] == "pass"
    assert "critic" in {r["component"] for r in con.execute("SELECT component FROM trace_steps WHERE case_id=?", (cid,))}
    assert cases.get_case(con, cid)["status"] != "needs_human"


def test_needs_human_verdict_flags_the_case_and_stays_unsent(con, monkeypatch):
    _fake_rag(monkeypatch, "Please email john.doe@example.com [1].")
    cid = cases.create_case(con, workflow="support")
    msg = "Ola Directions returns 429 for 80% of calls since 14:05 IST"
    pl = P.plan_case(con, cid, msg, submit=False)
    out = C.critique_case(con, cid, E.run_executor(con, cid, pl, msg), pl, chunks=CHUNKS)
    assert out["verdict"] == "needs_human" and cases.get_case(con, cid)["status"] == "needs_human"
    pending = [a for a in cases.list_pending(con, cid) if a["type"] == "external_send"][0]
    assert pending["status"] == "pending" and pending["payload"]["critic"]["verdict"] == "needs_human"
    assert any(i["check"] == "safety" for i in pending["payload"]["critic"]["issues"])


def test_decided_approvals_cannot_be_edited(con):
    aid = cases.request_approval(con, None, "external_send", {"draft": "x"})
    cases.decide(con, aid, approve=True, approver="deepshikha")
    with pytest.raises(ValueError):
        cases.update_pending_payload(con, aid, {"draft": "tampered"})
