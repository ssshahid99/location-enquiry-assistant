"""Customer-level history across cases (checklist A2).

A customer is identified by the name on the case (normalised: case and spacing ignored). There is no separate customers table: the
cases ARE the history, so nothing can get out of step. Earlier facts are SUGGESTIONS with provenance (which case, when, from which
message). They never enter a new case's known facts unless a person confirms them, and facts about a specific incident never carry over.
Rejected or stale suggestions can be dismissed. Everything is logged on the case timeline.
"""
from pathlib import Path

import yaml

from . import cases

_CFG = Path(__file__).resolve().parents[3] / "config" / "customers.yaml"


def _cfg() -> dict:
    return yaml.safe_load(_CFG.read_text(encoding="utf-8")) or {}


def customer_key(name: str | None) -> str | None:
    """Normalised identity, or None for anonymous customers (no history is built for them)."""
    k = " ".join((name or "").lower().split())
    return None if k in {a.lower() for a in _cfg().get("anonymous_names", [""])} else k


def earlier_cases(con, case_id: str) -> list[dict]:
    """Other cases of the same customer, newest first (the current case excluded)."""
    case = cases.get_case(con, case_id)
    key = customer_key(case["customer"]) if case else None
    if key is None:
        return []
    rows = con.execute("SELECT id, customer, status, category, provider, workflow, created_at, next_action FROM cases WHERE id<>? "
                       "ORDER BY created_at DESC", (case_id,)).fetchall()
    mine = [dict(r) for r in rows if customer_key(r["customer"]) == key]
    return mine[: _cfg().get("max_earlier_cases", 5)]


def suggest_prior_facts(con, case_id: str) -> list[dict]:
    """Setup facts the customer gave on earlier cases and that this case does not have yet. The NEWEST earlier value of each key wins.
    Each item: {key, value, from_case, from_date, message_id, status: 'unconfirmed'}."""
    allowed, have, out = set(_cfg().get("carry_forward_keys", [])), cases.known_facts(con, case_id), {}
    for ec in earlier_cases(con, case_id):                       # newest first, so setdefault keeps the newest value
        for r in con.execute("SELECT key, value, source_message_id, ts FROM case_facts WHERE case_id=?", (ec["id"],)):
            if r["key"] in allowed and r["key"] not in have:
                out.setdefault(r["key"], {"key": r["key"], "value": r["value"], "from_case": ec["id"], "from_date": ec["created_at"],
                                          "message_id": r["source_message_id"], "status": "unconfirmed"})
    return sorted(out.values(), key=lambda x: x["key"])


def attach_prior_facts(con, case_id: str) -> list[dict]:
    """Store the suggestions in the case state (NOT in the known facts) so the UI can show them for confirmation."""
    sug = suggest_prior_facts(con, case_id)
    state = cases.load_state(con, case_id)
    state["prior_facts"] = sug
    cases.save_state(con, case_id, state)
    if sug:
        cases.log_event(con, case_id, "system", "prior_facts_suggested", {"keys": [s["key"] for s in sug]})
    return sug


def pending_prior_facts(con, case_id: str) -> list[dict]:
    return [p for p in cases.load_state(con, case_id).get("prior_facts", []) if p["status"] == "unconfirmed"]


def _resolve(con, case_id: str, key: str, status: str, actor: str, value: str | None = None) -> dict:
    if not (actor or "").strip():
        raise ValueError("who is confirming this is required")
    state = cases.load_state(con, case_id)
    item = next((p for p in state.get("prior_facts", []) if p["key"] == key and p["status"] == "unconfirmed"), None)
    if item is None:
        raise ValueError(f"no unconfirmed earlier fact '{key}' on this case")
    if status == "confirmed":
        final = (value if value is not None else item["value"]).strip()
        if not final:
            raise ValueError("the value cannot be empty")
        cases.set_fact(con, case_id, key, final, None)           # now (and only now) it is a known fact of THIS case
        item["confirmed_value"] = final
    item["status"], item["resolved_by"] = status, actor.strip()
    cases.save_state(con, case_id, state)
    cases.log_event(con, case_id, actor.strip(), f"prior_fact_{status}",
                    {"key": key, "earlier_value": item["value"], "from_case": item["from_case"], "value": item.get("confirmed_value")})
    return item


def confirm_prior_fact(con, case_id: str, key: str, actor: str, value: str | None = None) -> dict:
    """The earlier fact is still true (or the customer gave a corrected `value`): it becomes a known fact of this case."""
    return _resolve(con, case_id, key, "confirmed", actor, value)


def dismiss_prior_fact(con, case_id: str, key: str, actor: str) -> dict:
    """The earlier fact no longer applies: it is dropped for this case and will be asked again by the Planner as usual."""
    return _resolve(con, case_id, key, "dismissed", actor)


def summary(con, case_id: str) -> dict:
    """Everything the UI needs: earlier cases (with their open/closed status) and the suggestions still waiting for confirmation."""
    return {"earlier_cases": earlier_cases(con, case_id), "unconfirmed_facts": pending_prior_facts(con, case_id)}
