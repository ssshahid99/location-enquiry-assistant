import time

import pytest

from lbs_rag.store import cases, review
from lbs_rag.store.db import connect


@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "r.db")


def _case_with_requests(con):
    cid = cases.create_case(con, workflow="support", type="incident")
    ids = {
        "external_send": cases.request_approval(con, cid, "external_send", {"draft": "We are looking into it."}),
        "tracker_update": cases.request_approval(con, cid, "tracker_update", {"changes": {"next_action": {"old": None, "new": "check logs"}}}),
        "owner_action": cases.request_approval(con, cid, "owner_action", {"owner_role": "support", "next_action": "triage", "urgency": "high"}),
        "handoff": cases.request_approval(con, cid, "handoff", {"text": "package", "assign_to": "deepshikha"}),
    }
    return cid, ids


def test_review_lists_everything_in_reading_order(con):
    cid, ids = _case_with_requests(con)
    r = review.review_case(con, cid)
    assert [i["type"] for i in r["items"]] == ["owner_action", "tracker_update", "external_send", "handoff"] and r["count"] == 4
    assert r["stale_count"] == 0
    with pytest.raises(ValueError):
        review.review_case(con, "nope")


def test_bundle_decides_each_request_individually_with_its_own_record(con):
    cid, ids = _case_with_requests(con)
    out = review.decide_bundle(con, cid, [{"action_id": ids["owner_action"], "approve": True},
                                          {"action_id": ids["tracker_update"], "approve": True},
                                          {"action_id": ids["external_send"], "approve": False, "note": "tone is wrong"}],
                               approver="deepshikha")
    assert out["done"] == 3 and out["failed"] == 0
    st = {a: cases.get_action(con, a) for a in ids.values()}
    assert st[ids["owner_action"]]["status"] == "approved" and st[ids["owner_action"]]["applied_at"]
    assert st[ids["external_send"]]["status"] == "rejected" and st[ids["external_send"]]["decision_note"] == "tone is wrong"
    assert st[ids["handoff"]]["status"] == "pending"                  # not included -> untouched, never auto-approved
    assert cases.get_tracker(con, cid)["next_action"]
    events = [e["event"] for e in cases.case_timeline(con, cid)]
    assert events.count("approval_approved") == 2 and events.count("approval_rejected") == 1 and "bundle_reviewed" in events


def test_one_failure_does_not_block_or_undo_the_others(con):
    cid, ids = _case_with_requests(con)
    out = review.decide_bundle(con, cid, [{"action_id": ids["owner_action"], "approve": True},
                                          {"action_id": ids["external_send"], "approve": False}],     # rejection with no reason
                               approver="deepshikha")
    ok = {r["action_id"]: r["ok"] for r in out["results"]}
    assert ok[ids["owner_action"]] is True and ok[ids["external_send"]] is False and out["failed"] == 1
    assert cases.get_action(con, ids["external_send"])["status"] == "pending"


def test_only_an_authorised_approver_can_decide_in_a_bundle(con):
    cid, ids = _case_with_requests(con)
    out = review.decide_bundle(con, cid, [{"action_id": ids["owner_action"], "approve": True}], approver="someone else")
    assert out["failed"] == 1 and "not an authorised approver" in out["results"][0]["error"]
    assert cases.get_action(con, ids["owner_action"])["status"] == "pending"


def test_stale_request_needs_explicit_acknowledgement_to_approve_but_not_to_reject(con):
    cid, ids = _case_with_requests(con)
    con.execute("UPDATE proposed_actions SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (ids["external_send"],))
    cases.add_message(con, cid, "customer", "Actually it is the Pune region too.")
    r = review.review_case(con, cid)
    stale = {i["id"]: i["stale"] for i in r["items"]}
    assert stale[ids["external_send"]] is True and r["stale_count"] >= 1
    out = review.decide_bundle(con, cid, [{"action_id": ids["external_send"], "approve": True}], approver="deepshikha")
    assert out["failed"] == 1 and "stale" in out["results"][0]["error"]
    assert cases.get_action(con, ids["external_send"])["status"] == "pending"
    out = review.decide_bundle(con, cid, [{"action_id": ids["external_send"], "approve": True, "acknowledge_stale": True}], approver="deepshikha")
    assert out["done"] == 1
    out2 = review.decide_bundle(con, cid, [{"action_id": ids["tracker_update"], "approve": False, "note": "outdated"}], approver="deepshikha")
    assert out2["done"] == 1


def test_malformed_bundles_are_refused_whole(con):
    cid, ids = _case_with_requests(con)
    other = cases.create_case(con)
    foreign = cases.request_approval(con, other, "owner_action", {"next_action": "x"})
    for bad in ([], [{"action_id": ids["owner_action"], "approve": True}, {"action_id": ids["owner_action"], "approve": False, "note": "x"}],
                [{"action_id": ids["owner_action"], "approve": True}, {"action_id": foreign, "approve": True}],
                [{"action_id": 99999, "approve": True}]):
        with pytest.raises(ValueError):
            review.decide_bundle(con, cid, bad, approver="deepshikha")
    assert cases.get_action(con, ids["owner_action"])["status"] == "pending"      # the valid half of a refused bundle was NOT applied
    assert cases.get_action(con, foreign)["status"] == "pending"


def test_critic_warnings_are_surfaced(con):
    cid = cases.create_case(con)
    cases.request_approval(con, cid, "external_send", {"draft": "x", "critic": {"verdict": "fail", "handoff_ready": False}})
    w = review.review_case(con, cid)["items"][0]["warnings"]
    assert any("Critic" in x for x in w) and any("handoff" in x.lower() for x in w)


def test_approving_a_bundle_never_sends_anything(con):
    cid, ids = _case_with_requests(con)
    out = review.decide_bundle(con, cid, [{"action_id": ids["external_send"], "approve": True}], approver="deepshikha")
    assert "nothing was sent" in out["results"][0]["result"].lower()
