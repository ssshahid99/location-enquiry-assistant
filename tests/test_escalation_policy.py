import pytest

from lbs_rag.safety import escalation_policy as P


@pytest.mark.parametrize("msg,cat,sev,owner", [
    ("PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.", "production_incident", "SEV1", "Maps engineering"),
    ("Our whole dispatch system is down: Ola Directions returns 429 for 80% of calls.", "production_incident", "SEV1", "Account team (Sales)"),
    ("Since this morning, Ola reverse-geocode returns 429 Reached monthly limit.", "production_incident", "SEV2", "Account team (Sales)"),
    ("URGENT: since 09:10 every Ola Directions call for Pune returns wrong routes", "production_incident", "SEV2", "Maps engineering"),
    ("Our prepaid Ola credits ran out and autocomplete stopped for paying users", "billing_incident", "SEV2", "Account team (Sales)"),
    ("A developer pushed our Ola Maps API key to a public GitHub repo", "security_incident", "SEV2", "Support L2"),
    ("Our Google Places bill exceeded our budget by 10x after a bot attack", "security_incident", "SEV3", "Support L2"),
    ("Ola Distance Matrix durations have been 40% lower than actual for weeks", "data_quality", "SEV2", "Maps engineering"),
    ("A customer reports Ola Autocomplete suggests a misspelt locality name", "data_quality", "SEV3", "Maps engineering"),
    ("We want 80M Ola Distance Matrix pairs a month with a committed-use contract", "contract_request", "SEV4", "Account team (Sales)"),
])
def test_policy_gives_the_severity_and_team_the_ground_truth_labels_specify(msg, cat, sev, owner):
    r = P.assess(msg, cat)
    assert (r["severity"], r["owner"]) == (sev, owner) and r["policy_version"].startswith("SC-02")


def test_non_escalating_cases_get_a_routine_owner_by_workflow():
    assert P.assess("how do I set language", None, "support")["owner"] == "Support L1"
    assert P.assess("what does it cost", None, "sales")["owner"] == "Account team (Sales)"
    assert P.assess("anything", None, "support")["severity"] == "SEV4"


def test_unknown_category_is_treated_as_routine_not_as_an_error():
    assert P.assess("x", "something_new", "support")["severity"] == "SEV4"


def test_every_category_in_the_escalation_rules_has_a_policy():
    import yaml
    from pathlib import Path
    rules = yaml.safe_load((Path(P._CFG).parents[0] / "escalation_rules.yaml").read_text(encoding="utf-8"))["rules"]
    missing = [r["category"] for r in rules if r["category"] not in P.policy()["categories"]]
    assert not missing, f"categories without an SC-02 policy entry: {missing}"


def test_the_planner_carries_severity_and_team_into_the_escalation_proposal():
    from lbs_rag.agent import planner
    p = planner.plan("PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.")
    e = p["escalation"]
    assert e["recommend"] and e["severity"] == "SEV1" and e["owner_team"] == "Maps engineering"
    payload = [x for x in p["proposals"] if x["type"] == "escalation"][0]["payload"]
    assert payload["severity"] == "SEV1" and payload["owner_team"] == "Maps engineering"
