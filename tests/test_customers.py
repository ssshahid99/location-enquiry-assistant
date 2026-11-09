import pytest

from lbs_rag.store import cases, customers
from lbs_rag.store.db import connect


@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "c.db")


def _old_case(con, customer="Acme Logistics", **facts):
    cid = cases.create_case(con, customer=customer)
    for k, v in facts.items():
        cases.set_fact(con, cid, k, v)
    return cid


def test_identity_ignores_case_and_spacing_and_anonymous_has_no_history(con):
    assert customers.customer_key("  ACME   Logistics ") == "acme logistics"
    assert customers.customer_key("unknown") is None and customers.customer_key("") is None and customers.customer_key(None) is None
    _old_case(con, "unknown", volume="1M")
    new = cases.create_case(con, customer="unknown")
    assert customers.earlier_cases(con, new) == [] and customers.suggest_prior_facts(con, new) == []


def test_suggestions_come_from_earlier_cases_of_the_same_customer_only(con):
    _old_case(con, "Acme Logistics", volume="2M requests/month", regions="Mumbai")
    _old_case(con, "Other Corp", volume="9B", regions="Delhi")
    new = cases.create_case(con, customer="acme  logistics")
    s = {x["key"]: x for x in customers.suggest_prior_facts(con, new)}
    assert set(s) == {"volume", "regions"} and s["volume"]["value"] == "2M requests/month" and s["volume"]["status"] == "unconfirmed"
    assert s["volume"]["from_case"] and s["volume"]["from_date"]                 # provenance


def test_incident_facts_never_carry_over(con):
    _old_case(con, error_response="HTTP 500", timestamp="2026-11-01 10:00", request_ids="abc", impact="all requests", provider_api="Ola Directions")
    new = cases.create_case(con, customer="Acme Logistics")
    assert [x["key"] for x in customers.suggest_prior_facts(con, new)] == ["provider_api"]


def test_newest_earlier_value_wins_and_current_facts_are_not_overridden(con):
    a = _old_case(con, volume="1M")
    con.execute("UPDATE cases SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (a,))
    _old_case(con, volume="5M", regions="Pune")
    new = cases.create_case(con, customer="Acme Logistics")
    cases.set_fact(con, new, "regions", "Chennai")                               # already known on this case
    s = {x["key"]: x["value"] for x in customers.suggest_prior_facts(con, new)}
    assert s == {"volume": "5M"}


def test_suggestions_are_not_known_facts_until_confirmed(con):
    _old_case(con, volume="2M")
    new = cases.create_case(con, customer="Acme Logistics")
    customers.attach_prior_facts(con, new)
    assert cases.known_facts(con, new) == {}                                      # NOT assumed
    assert [p["key"] for p in customers.pending_prior_facts(con, new)] == ["volume"]
    customers.confirm_prior_fact(con, new, "volume", "deepshikha")
    assert cases.known_facts(con, new) == {"volume": "2M"} and customers.pending_prior_facts(con, new) == []
    assert "prior_fact_confirmed" in {e["event"] for e in cases.case_timeline(con, new)}


def test_confirm_with_corrected_value_and_dismiss(con):
    _old_case(con, volume="2M", regions="Pune")
    new = cases.create_case(con, customer="Acme Logistics")
    customers.attach_prior_facts(con, new)
    customers.confirm_prior_fact(con, new, "volume", "deepshikha", value="4M")
    customers.dismiss_prior_fact(con, new, "regions", "deepshikha")
    assert cases.known_facts(con, new) == {"volume": "4M"}                       # dismissed one is dropped, will be asked again
    with pytest.raises(ValueError):
        customers.confirm_prior_fact(con, new, "volume", "deepshikha")           # already resolved
    with pytest.raises(ValueError):
        customers.dismiss_prior_fact(con, new, "nope", "deepshikha")


def test_confirmation_needs_a_named_person(con):
    _old_case(con, volume="2M")
    new = cases.create_case(con, customer="Acme Logistics")
    customers.attach_prior_facts(con, new)
    with pytest.raises(ValueError):
        customers.confirm_prior_fact(con, new, "volume", " ")


def test_orchestrator_attaches_suggestions_on_a_new_case_only(con, monkeypatch):
    import lbs_rag.agent.orchestrator as O
    monkeypatch.setattr(O, "answer", lambda q, **k: {"text": "x", "citations": [], "abstained": False, "refused": False, "escalate": False,
                                                       "reason": None, "trace_id": "t", "tool_calls": []})
    _old_case(con, provider_api="Ola Directions", volume="2M")
    r = O.handle_message(con, "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.",
                         customer="Acme Logistics", use_rag=False)
    assert r["route"] in ("agent", "human")
    assert {p["key"] for p in r["prior_facts"]} >= {"volume"} or {p["key"] for p in r["prior_facts"]} >= {"provider_api"}
    assert cases.known_facts(con, r["case_id"]).get("volume") is None
