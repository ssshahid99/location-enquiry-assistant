import pytest

from lbs_rag.agent import executor as E
from lbs_rag.agent import planner as P
from lbs_rag.store import cases
from lbs_rag.store.db import connect


@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "e.db")


def details(findings, check):
    return [f for f in findings if f["check"] == check]


# ------------------------------------------------------------------ validators
def test_swapped_coordinates_are_caught_for_a_lat_lng_api():
    f = details(E.check_inputs("Google Geocoding returns ZERO_RESULTS for 77.5946,12.9716"), "coordinates")
    assert f and f[0]["ok"] is False and "swapped" in f[0]["detail"]


def test_mapbox_expects_lng_lat_so_the_same_pair_is_fine():
    f = details(E.check_inputs("Mapbox Directions fails for 77.5946,12.9716"), "coordinates")
    assert f and f[0]["ok"] is True


def test_ola_matrix_too_big_gets_a_split_suggestion():
    f = details(E.check_inputs("Ola Distance Matrix 10x10 matrix returns an error"), "matrix_size")
    assert f and f[0]["ok"] is False and "pairs" in f[0]["detail"] and "Split into" in f[0]["detail"]


def test_waypoint_limit_for_ola_basic():
    f = details(E.check_inputs("Ola Directions Basic with 12 waypoints fails"), "waypoints")
    assert f and f[0]["ok"] is False


def test_timestamp_without_timezone_and_relative_dates():
    f = details(E.check_inputs("It broke at 14:05 yesterday"), "timestamp")
    assert any("no timezone" in x["detail"] for x in f) and any("relative date" in x["detail"] for x in f)
    assert not details(E.check_inputs("It broke at 14:05 IST on 2026-11-09"), "timestamp")


def test_response_shape_invalid_and_valid_json():
    assert details(E.check_inputs('Response: {"status": "ZERO_RESULTS", "results": ['), "response_shape")[0]["ok"] is False
    ok = details(E.check_inputs('Response: {"status": "ZERO_RESULTS", "results": []}'), "response_shape")[0]
    assert ok["ok"] is True and "error/status" in ok["detail"]


def test_ola_required_params_come_from_the_spec_and_other_providers_say_unknown():
    spec = E.ola_required_params()
    assert spec and any("directions" in p.lower() for p in spec)
    f = details(E.check_inputs("Ola Directions call: /routing/v1/directions?foo=bar returns 400"), "required_params")
    assert f and f[0]["ok"] is False and "required parameters not shown" in f[0]["detail"]
    g = details(E.check_inputs("Google Directions call with foo=bar fails"), "required_params")
    assert g and g[0]["ok"] is None and "No verified required-parameter list" in g[0]["detail"]


# ------------------------------------------------------------------ drafting
PLAN = {"questions": [{"key": "timestamp", "question": "When did this start?"}], "needs_human_followup": [],
        "escalation": {"recommend": True, "owner": "Ola support"}}


def test_draft_is_grounded_labelled_and_never_promises():
    know = {"text": "Ola allows 25 waypoints [1].", "citations": [{"n": 1, "provider": "Ola Maps", "doc_url": "https://maps.olakrutrim.com/docs"}]}
    caps = __import__("lbs_rag.tools.capabilities", fromlist=["x"]).match_capabilities("optimise stop order", ["Ola Maps", "HERE"])
    d = E.draft_communication("support_reply", plan=PLAN, knowledge=know,
                              validations=[{"check": "timestamp", "ok": False, "detail": "time has no timezone"}], capability_matches=caps)
    for needle in ("Ola allows 25 waypoints [1]", "Sources:", "[1] Ola Maps - https://maps.olakrutrim.com/docs", "When did this start?",
                   "time has no timezone", "[GAP]", "not promising any SLA", E.DRAFT_NOTE):
        assert needle in d, needle


def test_discovery_draft_uses_verified_capabilities_not_the_model_essay():
    from lbs_rag.tools.capabilities import match_capabilities
    know = {"text": "Mapbox is best and Ola Pro may support more waypoints [4].", "citations": [{"n": 4, "provider": "Mapbox", "doc_url": "https://x"}]}
    d = E.draft_communication("discovery", plan={"questions": []}, knowledge=know,
                              capability_matches=match_capabilities("optimise the order of stops", ["Ola Maps", "HERE"]))
    assert "Mapbox is best" not in d and "[GAP]" in d and "We only state what the documentation shows" in d
    d2 = E.draft_communication("discovery", plan={"questions": []}, knowledge=know, capability_matches=[])     # no evidence -> keep the answer
    assert "Mapbox is best" in d2


def test_refused_knowledge_is_not_pasted_into_a_draft():
    d = E.draft_communication("follow_up", plan={"questions": []}, knowledge={"text": "I can't help with this request", "refused": True})
    assert "I can't help" not in d and E.DRAFT_NOTE in d


# ------------------------------------------------------------------ orchestration (RAG stubbed: no model call in tests)
@pytest.fixture()
def fake_rag(monkeypatch):
    import lbs_rag.generate.answer as A
    monkeypatch.setattr(A, "answer", lambda q, **k: {
        "text": "Ola rate limits return 429 [1].", "citations": [{"n": 1, "provider": "Ola Maps", "doc_url": "https://maps.olakrutrim.com/docs", "fetched_at": "x"}],
        "abstained": False, "refused": False, "escalate": False, "reason": None})


def test_executor_runs_stores_findings_and_creates_one_pending_send_approval(con, fake_rag):
    cid = cases.create_case(con, workflow="support")
    msg = "Ola Directions returns 429 for 80% of calls since 14:05, request 12.9716,77.5946"
    pl = P.plan_case(con, cid, msg, submit=False)
    r = E.run_executor(con, cid, pl, msg)
    assert r["kind"] == "support_reply" and "Ola rate limits return 429" in r["draft"] and E.DRAFT_NOTE in r["draft"]
    assert any(v["check"] == "timestamp" and v["ok"] is False for v in r["validations"])
    pending = cases.list_pending(con, cid)
    assert sorted(a["type"] for a in pending) == ["external_send", "tracker_update"] and all(a["requested_by"] == "executor" for a in pending)
    E.run_executor(con, cid, pl, msg)                                     # a second run refreshes the pending requests, never piles up new ones
    assert len(cases.list_pending(con, cid)) == 2
    st = cases.load_state(con, cid)
    assert st["step"] == "draft_ready" and st["executor"]["draft"] and st["executor"]["citations"]
    comps = {row["component"] for row in con.execute("SELECT component FROM trace_steps WHERE case_id=?", (cid,))}
    assert "executor" in comps
    assert cases.get_case(con, cid)["status"] == "awaiting_approval" and cases.get_case(con, cid)["owner"] is None


def test_sales_case_gets_capability_table_and_discovery_draft(con, fake_rag):
    cid = cases.create_case(con, workflow="sales")
    msg = "We're a delivery app in Pune and need walking directions and to optimise the order of 20 stops. What do you recommend?"
    pl = P.plan_case(con, cid, msg, submit=False)
    r = E.run_executor(con, cid, pl, msg, submit=False)
    ids = {m["id"] for m in r["capabilities"]}
    assert r["kind"] == "discovery" and {"walking_cycling", "route_optimization"} <= ids
    assert "[GAP]" in r["draft"] and cases.list_pending(con, cid) == []


def test_handoff_package_is_complete_and_renderable(con, fake_rag):
    cid = cases.create_case(con, workflow="support", customer="Acme")
    msg = "Ola Directions returns 429 for most calls"
    pl = P.plan_case(con, cid, msg, submit=False)
    E.run_executor(con, cid, pl, msg, submit=False)
    pkg = E.build_handoff(con, cid)
    for k in ("case_id", "customer", "workflow", "category", "urgency", "requirements", "findings", "attempts", "open_questions",
              "next_action", "pending_approvals", "messages"):
        assert k in pkg, k
    assert pkg["customer"] == "Acme" and "timestamp" in pkg["open_questions"] and pkg["findings"]["knowledge_answer"]
    txt = E.render_handoff(pkg)
    assert "Handoff for case" in txt and "Open questions: timestamp" in txt


def test_followup_refreshes_the_pending_draft_and_extends_the_tracker_proposal(con, fake_rag):
    cid = cases.create_case(con, workflow="support")
    m1 = "Ola Directions returns 429 for most calls"
    pl1 = P.plan_case(con, cid, m1, submit=False)
    r1 = E.run_executor(con, cid, pl1, m1)
    m2 = "It started at 14:05 IST today"
    pl2 = P.plan_case(con, cid, m2, submit=False)
    r2 = E.run_executor(con, cid, pl2, m2)
    assert r2["approval_id"] == r1["approval_id"] and r2["tracker_approval_id"] == r1["tracker_approval_id"]
    tracker = cases.get_action(con, r1["tracker_approval_id"])["payload"]["changes"]
    assert "error_response" in tracker and "timestamp" in tracker and tracker["timestamp"]["old"] is None


def test_approved_tracker_update_writes_the_tracker_and_the_case(con, fake_rag):
    from lbs_rag.store import handlers
    cid = cases.create_case(con, workflow="support")
    m = "Ola Directions returns 429 for most calls since 14:05 IST"
    pl = P.plan_case(con, cid, m, submit=False)
    r = E.run_executor(con, cid, pl, m)
    assert cases.get_tracker(con, cid) == {}                                  # nothing in the approved record before approval
    handlers.approve_and_apply(con, r["tracker_approval_id"], approver="deepshikha")
    tr = cases.get_tracker(con, cid)
    assert "error_response" in tr and "timestamp" in tr and tr["category"] == "rate_limit"
    assert E.extract_changes(con, cid, pl) == {}                              # no further changes to propose


def test_lowercase_here_is_not_a_provider():
    from lbs_rag.retrieve.query_analyzer import detect
    assert "HERE" not in detect("Format the plus codes as shown here")["providers"]
    assert "HERE" in detect("HERE Routing v8 durations don't change")["providers"] and "HERE" in detect("what about here maps?")["providers"]


def test_tool_registry_exposes_the_expected_tools():
    names = set(E.tool_specs())
    assert {"rag_answer", "validate_inputs", "price_quote", "match_capabilities", "get_history", "known_facts", "build_handoff",
            "draft_communication"} <= names
