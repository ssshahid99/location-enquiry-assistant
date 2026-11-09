from lbs_rag.safety.escalation import check
from lbs_rag.store import repositories as repo


def test_outage_escalates():
    r = check("PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.")
    assert r["escalate"] and r["category"] == "production_incident"


def test_contract_sla_escalates():
    assert check("We want Ola autocomplete for 50M requests a month with an SLA and India-only data residency.")["category"] == "contract_request"


def test_leaked_key_escalates():
    assert check("A developer accidentally pushed our Ola Maps API key to a public GitHub repo.")["category"] == "security_incident"


def test_negation_does_not_escalate():
    assert not check("Ola Directions returns 429 during our morning spike. It's not an outage for us yet.")["escalate"]


def test_ordinary_401_in_production_does_not_escalate():
    assert not check("Our Mapbox Matrix call returns 401 in production.")["escalate"]


def test_plain_pricing_question_does_not_escalate():
    assert not check("What does Ola Maps Directions cost for 300,000 requests a month?")["escalate"]


def test_proposed_actions_require_human_decision(tmp_path):
    con = repo.connect(tmp_path / "t.db")
    aid = repo.create_proposed_action(con, type="escalation", payload={"x": 1})
    assert repo.list_pending(con)[0]["status"] == "pending"          # never auto-approved
    try:
        repo.decide_action(con, aid, approve=True, approver=" ")
        assert False, "blank approver must be rejected"
    except ValueError:
        pass
    repo.decide_action(con, aid, approve=True, approver="deepshikha")
    assert repo.list_pending(con) == []
    try:
        repo.decide_action(con, aid, approve=False, approver="deepshikha")   # cannot re-decide
        assert False
    except ValueError:
        pass
