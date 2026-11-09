import pytest

from lbs_rag.agent import planner as P
from lbs_rag.store import cases
from lbs_rag.store.db import connect


@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "p.db")


# ---------------------------------------------------------------- classification
def test_support_rate_limit():
    c = P.classify("Ola Directions returns 429 'Reached monthly/minute rate limits' during our morning dispatch spike.")
    assert (c["workflow"], c["category"]) == ("support", "rate_limit") and c["service"]["providers"] == ["Ola Maps"]


def test_production_outage_is_high_urgency():
    c = P.classify("PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.")
    assert (c["workflow"], c["category"], c["urgency"]) == ("support", "outage", "high")


def test_sales_discovery_and_pricing_and_contract():
    assert P.classify("Hi, we're a hyperlocal grocery app in Bengaluru and want a directions API for rider ETAs. What do you recommend?")["workflow"] == "sales"
    assert P.classify("What would Ola Maps Directions cost for 300,000 requests a month?")["category"] == "pricing"
    c = P.classify("We've committed to 40M Ola Directions calls a month and want negotiated pricing and an SLA.")
    assert (c["workflow"], c["category"]) == ("sales", "contract")


def test_billing_dispute_is_support_not_sales():
    assert P.classify("Ola billing shows Distance Matrix charges higher than our request count x rate. Is Ola overcharging?")["workflow"] == "support"


def test_security_and_data_quality_categories():
    assert P.classify("A developer accidentally pushed our Ola Maps API key to a public GitHub repo.")["category"] == "security"
    assert P.classify("Ola reverse geocode returns wrong city names since this morning")["category"] == "data_quality"


def test_unclear_message_asks_nothing_and_is_not_ready():
    r = P.plan("hello?")
    assert r["workflow"] == "unclear" and r["questions"] == [] and not r["ready_to_proceed"]


# ---------------------------------------------------------------- fact extraction
def test_extract_facts_from_a_rich_support_message():
    f = P.extract_facts("Ola Directions returns 500 for 80% of calls since 14:05 IST, e.g. 12.9716, 77.5946. request_id: abc123 after our deploy yesterday")
    for k in ("error_response", "timestamp", "impact", "coordinates", "request_ids", "recent_change", "provider", "api"):
        assert k in f, (k, f)


def test_extract_facts_sales_message():
    f = P.extract_facts("We run a delivery app in Pune, about 2M requests a month, budget Rs 2 lakh, go-live by March, need an SLA. Our CTO decides.")
    for k in ("use_case", "regions", "volume", "budget", "timeline", "constraints", "decision_maker"):
        assert k in f, (k, f)


def test_plain_error_message_is_troubleshooting_not_how_to():
    c = P.classify("Our Ola Distance Matrix 10x10 request returns an error since 14:05 yesterday.")
    assert (c["workflow"], c["category"]) == ("support", "troubleshooting") and c["urgency"] != "low"


def test_dimensions_are_not_a_volume():
    assert "volume" not in P.extract_facts("Our Ola Distance Matrix 10x10 request returns an error")
    assert "volume" in P.extract_facts("about 2M requests a month")


def test_prices_are_not_mistaken_for_error_codes():
    assert "error_response" not in P.extract_facts("Ola Directions for 500,000 requests a month, cost?")


# ---------------------------------------------------------------- gaps and questions
def test_discovery_asks_only_what_is_missing_and_at_most_four():
    r = P.plan("Hi, we're a hyperlocal grocery app in Bengaluru and want a directions/routing API for rider ETAs at checkout. What do you recommend?")
    keys = [q["key"] for q in r["questions"]]
    assert keys[0] == "volume" and "use_case" not in keys and "regions" not in keys and "api_needs" not in keys
    assert len(keys) <= 4 and r["missing_required"] == ["volume"] and not r["ready_to_proceed"]


def test_support_gaps_depend_on_category():
    r = P.plan("Ola Directions returns 429 for most calls")
    keys = [q["key"] for q in r["questions"]]
    assert "timestamp" in keys and "coordinates" not in keys           # coordinates only matter for coverage / data-quality issues
    r2 = P.plan("Ola reverse geocode returns wrong city names since this morning")
    assert any(i["key"] == "coordinates" and i["status"] == "missing" for i in r2["checklist"])


def test_complete_support_message_is_ready_to_proceed():
    r = P.plan("Ola Directions returns 429 for 80% of calls since 14:05 IST in production")
    assert r["missing_required"] == [] and r["ready_to_proceed"]


def test_how_to_question_is_not_interrogated_about_errors():
    r = P.plan("How do I restrict Ola Autocomplete to Hyderabad only?")
    assert r["workflow"] == "support" and r["category"] == "how_to"
    assert [q["key"] for q in r["questions"]] == [] and r["ready_to_proceed"]
    keys = [q["key"] for q in P.plan("Why does Google Distance Matrix return MAX_ELEMENTS_EXCEEDED for my request?")["questions"]]
    assert "provider_api" not in keys and "error_response" not in keys          # both already stated


def test_billing_option_in_a_price_question_is_sales_not_a_billing_dispute():
    c = P.classify("We're still on Place Autocomplete (Legacy) in India: 80,000 sessions x 7 requests. Cost per billing option?")
    assert c["workflow"] == "sales"
    assert P.classify("Our Google Distance Matrix bill doubled after we added side_of_road=true. Why?")["category"] == "billing"


def test_workflow_accuracy_on_clear_ground_truth_intents():
    """Regression guard on the development split (rules were tuned on it, so this is a floor, not a generalisation claim)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
    from load_gt import load_items
    from planner_eval import SALES, SUPPORT
    ok = tot = 0
    for it in load_items("eval"):
        exp = "sales" if it["intent"] in SALES else "support" if it["intent"] in SUPPORT else None
        if exp:
            tot += 1
            ok += P.plan(it["Question"])["workflow"] == exp
    assert ok / tot >= 0.95, (ok, tot)


# ---------------------------------------------------------------- escalation recommendation (proposed policy)
def test_outage_recommends_escalate_now_with_evidence():
    r = P.plan("PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.")
    e = r["escalation"]
    assert e["recommend"] and e["category"] == "production_incident" and e["mode"] == "now"
    assert e["missing_evidence"] == [] and "approved policy source" in e["policy_status"]


def test_data_quality_recommends_collecting_evidence_first():
    e = P.plan("A user reports Ola reverse geocoding shows their home in the wrong pin code.")["escalation"]
    assert e["recommend"] and e["mode"] == "after_info" and "coordinates" in e["missing_evidence"]


def test_ordinary_question_is_not_escalated():
    assert not P.plan("How do I restrict Ola Autocomplete to Hyderabad?")["escalation"]["recommend"]


# ---------------------------------------------------------------- persisted cases
def test_known_facts_are_never_asked_again(con):
    cid = cases.create_case(con, workflow="support")
    r1 = P.plan_case(con, cid, "Ola Directions returns 429 for most calls")
    assert "timestamp" in [q["key"] for q in r1["questions"]]
    r2 = P.plan_case(con, cid, "It started at 14:05 IST today and is still happening")
    assert "timestamp" not in [q["key"] for q in r2["questions"]]
    assert "timestamp" in cases.known_facts(con, cid) and "error_response" in cases.known_facts(con, cid)
    assert cases.get_case(con, cid)["category"] == "rate_limit"


def test_item_asked_twice_becomes_human_followup(con):
    cid = cases.create_case(con, workflow="support")
    P.plan_case(con, cid, "Ola Directions returns 429 for most calls")                  # asks timestamp (1)
    P.plan_case(con, cid, "please just fix it")                                          # asks again (2)
    r = P.plan_case(con, cid, "why is nobody helping")                                   # would be a 3rd ask
    assert "timestamp" in r["needs_human_followup"] and "timestamp" not in [q["key"] for q in r["questions"]]
    assert not r["ready_to_proceed"]


def test_proposals_become_pending_approvals_once_and_nothing_is_applied(con):
    cid = cases.create_case(con, workflow="support")
    msg = "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST."
    r1 = P.plan_case(con, cid, msg)
    types = sorted(a["type"] for a in cases.list_pending(con, cid))
    assert types == ["escalation", "owner_action"] and len(r1["submitted_approvals"]) == 2
    P.plan_case(con, cid, msg + " Still down.")                                          # no duplicates while pending
    assert len(cases.list_pending(con, cid)) == 2
    assert all(a["requested_by"] == "planner" and a["status"] == "pending" for a in cases.list_pending(con, cid))
    assert cases.get_case(con, cid)["owner"] is None                                     # the Planner never assigns or applies anything


def test_trace_steps_state_and_history_are_recorded(con):
    cid = cases.create_case(con)
    r = P.plan_case(con, cid, "Ola Directions returns 429 for most calls", submit=False)
    steps = [row["step"] for row in con.execute("SELECT step FROM trace_steps WHERE case_id=?", (cid,))]
    assert {"classify", "extract_facts", "gap_analysis", "escalation_check"} <= set(steps)
    st = cases.load_state(con, cid)
    assert st["step"] == "awaiting_customer" and st["asked_counts"]["timestamp"] == 1 and st["last_trace_id"] == r["trace_id"]
    roles = [m["role"] for m in cases.get_case_history(con, cid)]
    assert roles == ["customer", "agent"]                                                # the questions asked are part of the history
    assert cases.list_pending(con, cid) == []                                            # submit=False creates nothing


def test_provider_is_not_blanked_by_a_later_message(con):
    cid = cases.create_case(con)
    P.plan_case(con, cid, "Ola Directions returns 429 for most calls", submit=False)
    P.plan_case(con, cid, "started at 14:05 IST", submit=False)
    assert cases.get_case(con, cid)["provider"] == "Ola Maps"


def test_http_status_codes_are_not_a_request_volume():
    # found by the demo-scenario acceptance test: "returns 500 for some requests" was recorded as a stated volume
    f = P.extract_facts("Ola Directions returns 500 for some requests since this morning.")
    assert "volume" not in f
    assert P.extract_facts("We expect about 2 million lookups a month")["volume"]
    assert P.extract_facts("Our traffic is 500 requests per minute")["volume"]          # a real volume still counts