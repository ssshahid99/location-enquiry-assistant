import sqlite3
import time

import pytest

from lbs_rag.store import cases, repositories as repo
from lbs_rag.store.db import connect


@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "t.db")


def test_case_history_facts_and_summary(con):
    cid = cases.create_case(con, customer="Acme", workflow="support", category="rate_limit", provider="Ola Maps")
    m1 = cases.add_message(con, cid, "customer", "Ola Directions returns 429 since 14:05 IST for Mumbai coordinates")
    cases.add_message(con, cid, "agent", "Please share request ids and timestamps")
    cases.set_fact(con, cid, "Region", "Mumbai", m1)
    cases.set_fact(con, cid, "first_error_time", "14:05 IST", m1)
    cases.set_fact(con, cid, "region", "Mumbai and Pune", m1)               # a fact is updated, not duplicated
    assert cases.known_facts(con, cid) == {"first_error_time": "14:05 IST", "region": "Mumbai and Pune"}
    s = cases.summarize_history(con, cid)
    assert "region=Mumbai and Pune" in s and "429" in s and "request ids" in s
    assert [m["role"] for m in cases.get_case_history(con, cid)] == ["customer", "agent"]


def test_only_listed_fields_can_be_updated_and_events_are_logged(con):
    cid = cases.create_case(con)
    cases.update_case(con, cid, owner="deepshikha", next_action="collect request ids", actor="deepshikha")
    assert cases.get_case(con, cid)["owner"] == "deepshikha"
    with pytest.raises(ValueError):
        cases.update_case(con, cid, status_json="x")
    events = [e["event"] for e in cases.case_timeline(con, cid)]
    assert events == ["case_created", "case_updated"]


def test_state_checkpoint_roundtrip(con):
    cid = cases.create_case(con)
    cases.save_state(con, cid, {"step": "awaiting_approval", "draft": "Hi", "asked": ["timestamps"]})
    assert cases.load_state(con, cid)["asked"] == ["timestamps"]
    assert cases.load_state(con, cases.create_case(con)) == {}


def test_approval_flow_requests_decides_and_applies_once(con):
    cid = cases.create_case(con, workflow="support", type="incident", category="production_incident")
    aid = cases.request_approval(con, cid, "escalation", {"draft": "Escalate to Ola support"})
    assert cases.get_case(con, cid)["status"] == "awaiting_approval"
    assert [a["id"] for a in cases.list_pending(con)] == [aid]

    done = cases.decide(con, aid, approve=True, approver="Deepshikha", note="ok", edited_payload={"draft": "Escalate to Ola support NOW"})
    assert done["status"] == "approved" and cases.get_case(con, cid)["status"] == "approved_pending_apply"

    seen = []
    res = cases.apply_approved(con, aid, lambda p: seen.append(p) or {"ok": True})
    assert seen == [{"draft": "Escalate to Ola support NOW"}]          # the approver's edit wins
    assert res["applied_at"] and cases.get_case(con, cid)["status"] == "in_progress"
    with pytest.raises(ValueError, match="already applied"):
        cases.apply_approved(con, aid, lambda p: seen.append("again"))      # cannot apply twice
    assert len(seen) == 1
    events = [e["event"] for e in cases.case_timeline(con, cid)]
    assert events == ["case_created", "approval_requested", "approval_approved", "action_applied"]


def test_approver_rules_single_authorised_human(con):
    aid = cases.request_approval(con, None, "tracker_update", {"x": 1})
    for bad in ("agent", "", "someone else"):
        with pytest.raises(ValueError):
            cases.decide(con, aid, approve=True, approver=bad)
    with pytest.raises(ValueError, match="reason is required"):
        cases.decide(con, aid, approve=False, approver="deepshikha")
    cases.decide(con, aid, approve=False, approver="deepshikha", note="wrong customer")
    with pytest.raises(ValueError, match="already rejected"):
        cases.decide(con, aid, approve=True, approver="deepshikha")           # a decision is final
    with pytest.raises(ValueError, match="only an approved action"):
        cases.apply_approved(con, aid, lambda p: None)                         # rejected actions are never applied


def test_adding_an_approver_is_a_config_change(monkeypatch, tmp_path):
    cfg = tmp_path / "approvers.yaml"
    cfg.write_text("required_approvals: 1\napprovers:\n  - name: deepshikha\n    can_approve: all\n"
                   "  - name: asha\n    can_approve: [tracker_update]\n", encoding="utf-8")
    monkeypatch.setattr(cases, "ROOT", tmp_path.parent)                         # not used; we patch the loader instead
    import yaml
    monkeypatch.setattr(cases, "_approvers_cfg", lambda: yaml.safe_load(cfg.read_text(encoding="utf-8")))
    assert cases.can_approve("Asha", "tracker_update") and not cases.can_approve("asha", "escalation")
    assert cases.can_approve("deepshikha", "escalation") and not cases.can_approve("agent", "escalation")


def test_unknown_action_type_is_rejected(con):
    with pytest.raises(ValueError):
        cases.request_approval(con, None, "delete_everything", {})


def test_failed_apply_is_recorded_not_silently_lost(con):
    aid = cases.request_approval(con, None, "tracker_update", {"x": 1})
    cases.decide(con, aid, approve=True, approver="deepshikha")
    with pytest.raises(RuntimeError):
        cases.apply_approved(con, aid, lambda p: (_ for _ in ()).throw(RuntimeError("tracker down")))
    assert "tracker down" in cases.get_action(con, aid)["applied_result"]


def test_trace_steps_are_stored(con):
    t0 = time.time()
    cases.trace_step(con, "tr1", "retrieve", "executor", {"q": "x"}, {"hits": 6}, t0, case_id="c1")
    row = con.execute("SELECT step, component, ms FROM trace_steps WHERE trace_id='tr1'").fetchone()
    assert row["step"] == "retrieve" and row["component"] == "executor" and row["ms"] >= 0


def test_old_database_is_upgraded_without_losing_data(tmp_path):
    p = tmp_path / "old.db"
    old = sqlite3.connect(p)
    old.execute("CREATE TABLE cases (id TEXT PRIMARY KEY, customer TEXT, status TEXT, owner TEXT, created_at TEXT)")
    old.execute("CREATE TABLE proposed_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, type TEXT, payload_json TEXT, "
                "status TEXT DEFAULT 'pending', approver TEXT, created_at TEXT, decided_at TEXT)")
    old.execute("INSERT INTO cases VALUES ('c0','Acme','open',NULL,'2026-11-01')")
    old.execute("INSERT INTO proposed_actions (case_id, type, payload_json, created_at) VALUES ('c0','escalation','{}','2026-11-01')")
    old.commit()
    old.close()
    con = connect(p)
    assert cases.get_case(con, "c0")["customer"] == "Acme"
    assert cases.list_pending(con)[0]["type"] == "escalation"
    cases.decide(con, 1, approve=True, approver="deepshikha")                  # new columns work on the upgraded table
    assert cases.get_action(con, 1)["status"] == "approved"


def test_backward_compatible_repository_api(con):
    aid = repo.create_proposed_action(con, type="escalation", payload={"x": 1})
    assert repo.list_pending(con)[0]["status"] == "pending"
    with pytest.raises(ValueError):
        repo.decide_action(con, aid, approve=True, approver=" ")
    repo.decide_action(con, aid, approve=True, approver="deepshikha")
    assert repo.list_pending(con) == []
