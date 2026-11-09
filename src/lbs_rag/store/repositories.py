"""Persistence helpers. Consequential actions are only ever *proposed* here; one authorised human decides (see store/cases.py).

The approval functions below keep their original names (used by the API, UI and tests) and delegate to cases.py, which enforces the
rules: authorised approver only, rejection needs a reason, a decision is final, an approved action is applied at most once.
"""
import json
from datetime import datetime, timezone

from . import cases
from .db import connect


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_trace(con, *, trace_id: str, query: str, a: dict, model: str) -> None:
    con.execute("INSERT OR REPLACE INTO traces (id, ts, query, retrieved_json, tool_calls_json, model, abstained, escalate, answer) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (trace_id, _now(), query, json.dumps(a.get("retrieved", [])), json.dumps(a.get("tool_calls", []), default=str),
                 model, int(a["abstained"]), int(a["escalate"]), a["text"]))
    con.commit()


def create_proposed_action(con, *, type: str, payload: dict, case_id: str | None = None, requested_by: str = "agent") -> int:
    """Status is always 'pending'. Nothing is sent or applied until a human approves."""
    return cases.request_approval(con, case_id, type, payload, requested_by=requested_by)


def list_pending(con, case_id: str | None = None) -> list[dict]:
    return cases.list_pending(con, case_id)


def decide_action(con, action_id: int, *, approve: bool, approver: str, note: str | None = None,
                  edited_payload: dict | None = None) -> dict:
    """Record one authorised human decision (rejection needs a note). Executing an approved action is a separate step."""
    return cases.decide(con, action_id, approve=approve, approver=approver, note=note, edited_payload=edited_payload)


__all__ = ["connect", "save_trace", "create_proposed_action", "list_pending", "decide_action", "cases"]
