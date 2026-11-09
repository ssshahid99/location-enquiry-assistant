"""The 8 demo scenarios as an acceptance test (A7). The scenarios are our own choice (the project document does not list them): they
cover every route and every safety rule a demo should show. Offline: the RAG answer is stubbed and the Executor does not call it, so no
model is used; the Router, Planner, Executor, Critic, approvals and case store are the real ones."""
import pytest

from lbs_rag.agent import orchestrator as O
from lbs_rag.store import cases, customers, handlers, review
from lbs_rag.store.db import connect


@pytest.fixture()
def con(tmp_path, monkeypatch):
    from lbs_rag.generate import guard

    def fake_answer(q, **k):            # the real pipeline refuses what the guard refuses; everything else gets a canned grounded-looking reply
        refused = guard.check(q)["refuse"] or guard.domain_gate(q, None) is not None
        return {"text": "Not processed." if refused else "Ola rate limiting returns a 429 response [1].", "citations": [], "abstained": refused,
                "refused": refused, "escalate": False, "reason": None, "trace_id": "t", "tool_calls": []}
    monkeypatch.setattr(O, "answer", fake_answer)
    return connect(tmp_path / "demo.db")


def run(con, msg, **kw):
    return O.handle_message(con, msg, use_rag=False, **kw)


def test_1_plain_price_question_is_answered_directly_without_a_case(con):
    r = run(con, "What does Ola Maps Reverse Geocoding cost for 20,000,000 lookups a month?")
    assert r["route"] == "rag" and r["case_id"] is None and not cases.list_cases(con)


def test_2_production_outage_becomes_a_case_with_every_change_waiting_for_approval(con):
    r = run(con, "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.", customer="Acme Logistics")
    cid = r["case_id"]
    assert r["route"] == "agent" and r["critic"]["verdict"] in ("pass", "revised", "fail") and r["draft"]
    pend = cases.list_pending(con, cid)
    assert len(pend) >= 3 and all(p["status"] == "pending" for p in pend)
    assert cases.get_case(con, cid)["owner"] is None and cases.get_tracker(con, cid) == {}          # nothing applied
    assert any("escalation" == p["type"] for p in pend)


def test_3_vague_problem_asks_at_most_four_targeted_questions_and_never_repeats_known_facts(con):
    r = run(con, "Our Google Distance Matrix calls keep failing.", customer="Beta Cabs")
    assert 1 <= len(r["questions"]) <= 4
    asked_first = {q["key"] for q in r["questions"]}
    r2 = O.reply(con, r["case_id"], "It started at 14:05 IST today and returns OVER_QUERY_LIMIT.", use_rag=False)
    assert not ({q["key"] for q in r2["questions"]} & {"timestamp", "error_response"})              # answered, so not asked again
    assert asked_first - {q["key"] for q in r2["questions"]}


def test_4_sales_discovery_gets_qualification_questions_and_a_capability_comparison(con):
    r = run(con, "We are building a rider app on Ola Maps and need to optimise the order of 20 stops. Which provider should we use?", customer="Gamma Rides")
    assert r["route"] == "agent" and r["workflow"] == "sales"
    assert {c["id"] for c in r["capabilities"]} >= {"route_optimization"}
    assert "[GAP]" in r["draft"] or "Not documented" in r["draft"] or "documented" in r["draft"].lower()


def test_5_contract_request_goes_to_a_person_with_a_handoff_and_no_customer_draft(con):
    r = run(con, "We need a custom SLA with 99.99% uptime and committed-use pricing for our enterprise agreement.", customer="Delta Corp")
    assert r["route"] == "human" and r["draft"] is None and r["handoff"]
    assert cases.get_case(con, r["case_id"])["status"] == "needs_human"


def test_6_leaked_key_is_a_human_case_and_the_secret_is_never_stored(con):
    secret = "sk-lm4-FAKE0KEY1234567890abcdefXYZ"
    r = run(con, f"Our Mapbox key {secret} was pushed to a public GitHub repo, what do we do?", customer="Eps Tech")
    assert r["route"] in ("human", "agent") and r["redacted"]
    stored = " ".join(m["content"] for m in cases.get_case_history(con, r["case_id"]))
    assert secret not in stored


@pytest.mark.parametrize("msg", ["What is the capital of France?", "Write a script to scrape and track my ex's location without them knowing."])
def test_7_out_of_scope_and_unethical_requests_are_refused_and_open_no_case(con, msg):
    r = run(con, msg)
    assert r["route"] == "refused" and r["case_id"] is None and not cases.list_cases(con)


def test_8_full_lifecycle_review_approve_close_reopen_and_returning_customer(con):
    r = run(con, "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.", customer="Zeta Foods")
    cid = r["case_id"]
    rv = review.review_case(con, cid)                                               # one screen for the whole case
    assert rv["count"] >= 3
    out = review.decide_bundle(con, cid, [{"action_id": i["id"], "approve": True} for i in rv["items"] if i["type"] in ("owner_action", "tracker_update")],
                               approver="deepshikha")
    assert out["failed"] == 0
    still = review.review_case(con, cid)["items"]
    assert still                                                                    # escalation / reply draft are still waiting
    bad = review.decide_bundle(con, cid, [{"action_id": still[0]["id"], "approve": True}], approver="an agent")      # unauthorised
    assert bad["failed"] == 1 and "not an authorised approver" in bad["results"][0]["error"]
    cases.close_case(con, cid, approver="deepshikha", note="resolved by customer")
    with pytest.raises(ValueError):
        O.reply(con, cid, "still broken", use_rag=False)                            # closed: must be reopened first
    cases.reopen_case(con, cid, approver="deepshikha", note="customer says it is back")
    # a returning customer: earlier setup facts are offered, never assumed
    cases.set_fact(con, cid, "volume", "5M requests/month")
    r2 = run(con, "Ola Directions returns 500 for some requests since this morning.", customer="zeta  foods")
    assert "volume" in {p["key"] for p in r2["prior_facts"]} and "volume" not in cases.known_facts(con, r2["case_id"])
    customers.confirm_prior_fact(con, r2["case_id"], "volume", "deepshikha")
    assert cases.known_facts(con, r2["case_id"])["volume"] == "5M requests/month"
