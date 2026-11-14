"""Cases, history, known facts, approvals and traced steps (foundation for the Planner/Executor/Critic agent).

Rules enforced here (not left to callers):
- Approval is ALWAYS by one authorised human from config/approvers.yaml; the agent can never approve; rejection needs a reason.
- A decision is final; an approved action is applied AT MOST ONCE (apply_approved claims it atomically).
- Every state change writes a case event (who, what, when) for traceability.
"""
import json
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
ACTION_TYPES = {"escalation", "tracker_update", "handoff", "external_send", "owner_action", "other"}
_CASE_FIELDS = {"customer", "status", "owner", "type", "workflow", "provider", "api", "category", "urgency", "next_action"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ approvers
@lru_cache(maxsize=1)
def _approvers_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "approvers.yaml").read_text(encoding="utf-8"))


def reload_approvers() -> None:
    _approvers_cfg.cache_clear()


def approver_names() -> list[str]:
    return [a["name"] for a in _approvers_cfg()["approvers"]]


def can_approve(name: str, action_type: str) -> bool:
    n = (name or "").strip().lower()
    for a in _approvers_cfg()["approvers"]:
        if a["name"].strip().lower() == n:
            allowed = a.get("can_approve", "all")
            return allowed == "all" or action_type in allowed
    return False


# ------------------------------------------------------------------ cases
def log_event(con, case_id: str | None, actor: str, event: str, detail: dict | None = None) -> None:
    con.execute("INSERT INTO case_events (case_id, ts, actor, event, detail_json) VALUES (?,?,?,?,?)",
                (case_id, _now(), actor, event, json.dumps(detail or {}, ensure_ascii=False, default=str)))
    con.commit()


def create_case(con, *, customer: str = "unknown", workflow: str = "support", type: str = "case", provider: str | None = None,
                api: str | None = None, category: str | None = None, urgency: str | None = None, owner: str | None = None) -> str:
    cid = uuid.uuid4().hex[:10]
    con.execute("INSERT INTO cases (id, customer, status, owner, created_at, type, workflow, provider, api, category, urgency, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, customer, "open", owner, _now(), type, workflow, provider, api, category, urgency, _now()))
    con.commit()
    log_event(con, cid, "system", "case_created", {"type": type, "workflow": workflow, "category": category})
    return cid


def get_case(con, case_id: str) -> dict | None:
    r = con.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    return dict(r) if r else None


def update_case(con, case_id: str, *, actor: str = "system", **fields) -> None:
    bad = set(fields) - _CASE_FIELDS
    if bad:
        raise ValueError(f"cannot update case fields {sorted(bad)}")
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
    con.execute(f"UPDATE cases SET {sets} WHERE id=?", (*fields.values(), _now(), case_id))
    con.commit()
    log_event(con, case_id, actor, "case_updated", fields)


def add_message(con, case_id: str, role: str, content: str) -> int:
    cur = con.execute("INSERT INTO messages (case_id, role, content, ts) VALUES (?,?,?,?)", (case_id, role, content, _now()))
    con.commit()
    return cur.lastrowid


# ------------------------------------------------------------------ history and known facts
def get_case_history(con, case_id: str, limit: int = 50) -> list[dict]:
    rows = con.execute("SELECT id, role, content, ts FROM messages WHERE case_id=? ORDER BY id DESC LIMIT ?", (case_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def set_fact(con, case_id: str, key: str, value: str, source_message_id: int | None = None) -> None:
    con.execute("INSERT OR REPLACE INTO case_facts (case_id, key, value, source_message_id, ts) VALUES (?,?,?,?,?)",
                (case_id, key.strip().lower(), str(value), source_message_id, _now()))
    con.commit()


def known_facts(con, case_id: str) -> dict[str, str]:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM case_facts WHERE case_id=? ORDER BY key", (case_id,))}


def summarize_history(con, case_id: str, max_chars: int = 1500) -> str:
    """Deterministic summary (no model): known facts first, then the most recent messages, truncated. Used so the agent checks what
    is already known before asking the customer again."""
    facts = known_facts(con, case_id)
    parts = []
    if facts:
        parts.append("Known facts: " + "; ".join(f"{k}={v}" for k, v in facts.items()))
    recent = get_case_history(con, case_id, limit=8)
    for m in recent:
        parts.append(f"[{m['role']}] {m['content'][:240]}")
    return "\n".join(parts)[:max_chars]


def save_state(con, case_id: str, state: dict) -> None:
    """Checkpoint for pause/resume: the agent saves where it stopped when it reaches an approval gate."""
    con.execute("UPDATE cases SET state_json=?, updated_at=? WHERE id=?", (json.dumps(state, ensure_ascii=False, default=str), _now(), case_id))
    con.commit()


def load_state(con, case_id: str) -> dict:
    r = con.execute("SELECT state_json FROM cases WHERE id=?", (case_id,)).fetchone()
    return json.loads(r["state_json"]) if r and r["state_json"] else {}


# ------------------------------------------------------------------ tracker (the APPROVED operational record) and case listing
def get_tracker(con, case_id: str) -> dict[str, str]:
    return {r["field"]: r["value"] for r in con.execute("SELECT field, value FROM case_tracker WHERE case_id=? ORDER BY field", (case_id,))}


def set_tracker(con, case_id: str, field: str, value: str, *, approved_by: str, source_action_id: int | None = None) -> None:
    """Write one tracker field. Only called when applying an APPROVED tracker_update (see store/handlers.py)."""
    con.execute("INSERT OR REPLACE INTO case_tracker (case_id, field, value, approved_by, approved_at, source_action_id) VALUES (?,?,?,?,?,?)",
                (case_id, field, str(value), approved_by, _now(), source_action_id))
    con.commit()


def list_cases(con, status: str | None = None, limit: int = 100) -> list[dict]:
    q, args = "SELECT * FROM cases", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    return [dict(r) for r in con.execute(q + " ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?", (*args, limit))]


# ------------------------------------------------------------------ approvals
def request_approval(con, case_id: str | None, action_type: str, payload: dict, requested_by: str = "agent") -> int:
    """Create a PENDING approval request and put the case into 'awaiting_approval'. Nothing is applied or sent."""
    if action_type not in ACTION_TYPES:
        raise ValueError(f"unknown action type '{action_type}'")
    cur = con.execute("INSERT INTO proposed_actions (case_id, type, payload_json, status, created_at, requested_by) VALUES (?,?,?,'pending',?,?)",
                      (case_id, action_type, json.dumps(payload, ensure_ascii=False, default=str), _now(), requested_by))
    con.commit()
    aid = cur.lastrowid
    if case_id:
        con.execute("UPDATE cases SET status='awaiting_approval', updated_at=? WHERE id=?", (_now(), case_id))
        con.commit()
    log_event(con, case_id, requested_by, "approval_requested", {"action_id": aid, "type": action_type})
    return aid


def get_action(con, action_id: int) -> dict | None:
    r = con.execute("SELECT * FROM proposed_actions WHERE id=?", (action_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["payload"] = json.loads(d["payload_json"])
    d["edited_payload"] = json.loads(d["edited_payload_json"]) if d.get("edited_payload_json") else None
    return d


def update_pending_payload(con, action_id: int, payload: dict, *, actor: str = "system") -> None:
    """Replace the payload of a request that is STILL pending (for example with a Critic-revised draft). Decided requests are immutable."""
    a = get_action(con, action_id)
    if a is None or a["status"] != "pending":
        raise ValueError("only a pending request can be updated")
    con.execute("UPDATE proposed_actions SET payload_json=? WHERE id=? AND status='pending'",
                (json.dumps(payload, ensure_ascii=False, default=str), action_id))
    con.commit()
    log_event(con, a["case_id"], actor, "approval_payload_updated", {"action_id": action_id})


def decide(con, action_id: int, *, approve: bool, approver: str, note: str | None = None, edited_payload: dict | None = None) -> dict:
    """One authorised human decides. Raises ValueError with a clear reason otherwise."""
    a = get_action(con, action_id)
    if a is None:
        raise ValueError(f"action {action_id} not found")
    if a["status"] != "pending":
        raise ValueError(f"action {action_id} was already {a['status']}")
    if not (approver or "").strip():
        raise ValueError("an approver name is required")
    if not can_approve(approver, a["type"]):
        raise ValueError(f"'{approver}' is not an authorised approver for '{a['type']}' (allowed: {', '.join(approver_names())})")
    if not approve and not (note or "").strip():
        raise ValueError("a reason is required to reject")
    status = "approved" if approve else "rejected"
    cur = con.execute("UPDATE proposed_actions SET status=?, approver=?, decided_at=?, decision_note=?, edited_payload_json=? "
                      "WHERE id=? AND status='pending'",
                      (status, approver.strip(), _now(), note, json.dumps(edited_payload, ensure_ascii=False) if edited_payload else None, action_id))
    con.commit()
    if cur.rowcount != 1:
        raise ValueError(f"action {action_id} was decided by someone else just now")
    if a["case_id"]:
        con.execute("UPDATE cases SET status=?, updated_at=? WHERE id=?",
                    ("approved_pending_apply" if approve else "rejected", _now(), a["case_id"]))
        con.commit()
    log_event(con, a["case_id"], approver.strip(), f"approval_{status}", {"action_id": action_id, "note": note, "edited": bool(edited_payload)})
    return get_action(con, action_id)


def apply_approved(con, action_id: int, handler) -> dict:
    """Apply an APPROVED action exactly once. `handler(payload) -> dict|str|None` does the internal update (never an external send).
    The claim is atomic, so a double click or a retry cannot apply twice."""
    a = get_action(con, action_id)
    if a is None or a["status"] != "approved":
        raise ValueError("only an approved action can be applied")
    cur = con.execute("UPDATE proposed_actions SET applied_at=? WHERE id=? AND status='approved' AND applied_at IS NULL", (_now(), action_id))
    con.commit()
    if cur.rowcount != 1:
        raise ValueError(f"action {action_id} was already applied")
    payload = a["edited_payload"] or a["payload"]          # the approver's edited version wins
    try:
        result = handler(payload)
        con.execute("UPDATE proposed_actions SET applied_result=? WHERE id=?", (json.dumps(result, default=str), action_id))
        if a["case_id"]:       # only move the case on if the handler did not already set a more specific status (e.g. 'escalated')
            con.execute("UPDATE cases SET status='in_progress', updated_at=? WHERE id=? AND status='approved_pending_apply'",
                        (_now(), a["case_id"]))
        con.commit()
        log_event(con, a["case_id"], "system", "action_applied", {"action_id": action_id, "type": a["type"]})
    except Exception as e:  # noqa: BLE001
        con.execute("UPDATE proposed_actions SET applied_result=? WHERE id=?", (f"error: {type(e).__name__}: {e}", action_id))
        con.commit()
        log_event(con, a["case_id"], "system", "apply_failed", {"action_id": action_id, "error": str(e)})
        raise
    return get_action(con, action_id)


def list_pending(con, case_id: str | None = None) -> list[dict]:
    q, args = "SELECT id FROM proposed_actions WHERE status='pending'", ()
    if case_id:
        q, args = q + " AND case_id=?", (case_id,)
    return [get_action(con, r["id"]) for r in con.execute(q + " ORDER BY id", args)]


def close_case(con, case_id: str, *, approver: str, note: str) -> dict:
    """Close a case. An operational update, so only a listed human may do it, with a written resolution. Anything still waiting for
    approval on the case is CANCELLED (never applied), and the case history and timeline record who closed it and why."""
    c = get_case(con, case_id)
    if c is None:
        raise ValueError(f"case {case_id} not found")
    if c["status"] == "closed":
        raise ValueError(f"case {case_id} is already closed")
    if not (approver or "").strip():
        raise ValueError("an approver name is required")
    if not can_approve(approver, "other"):
        raise ValueError(f"'{approver}' is not an authorised approver (allowed: {', '.join(approver_names())})")
    if not (note or "").strip():
        raise ValueError("a resolution note is required to close a case")
    pending = list_pending(con, case_id)
    for a in pending:
        con.execute("UPDATE proposed_actions SET status='cancelled', approver=?, decided_at=?, decision_note=? WHERE id=? AND status='pending'",
                    (approver.strip(), _now(), f"cancelled: case closed ({note.strip()[:200]})", a["id"]))
    con.commit()
    con.execute("UPDATE cases SET status='closed', next_action=?, updated_at=? WHERE id=?", (f"Closed: {note.strip()[:300]}", _now(), case_id))
    con.commit()
    add_message(con, case_id, "approver", f"[case closed] {note.strip()}")
    log_event(con, case_id, approver.strip(), "case_closed", {"note": note.strip(), "cancelled_requests": [a["id"] for a in pending]})
    return get_case(con, case_id)


def reopen_case(con, case_id: str, *, approver: str, note: str) -> dict:
    """Reopen a closed case (same authorisation rules). Cancelled requests stay cancelled; the Planner will propose fresh ones."""
    c = get_case(con, case_id)
    if c is None:
        raise ValueError(f"case {case_id} not found")
    if c["status"] != "closed":
        raise ValueError(f"case {case_id} is not closed")
    if not can_approve(approver, "other"):
        raise ValueError(f"'{approver}' is not an authorised approver (allowed: {', '.join(approver_names())})")
    if not (note or "").strip():
        raise ValueError("a reason is required to reopen a case")
    con.execute("UPDATE cases SET status='open', next_action=?, updated_at=? WHERE id=?", (f"Reopened: {note.strip()[:300]}", _now(), case_id))
    con.commit()
    add_message(con, case_id, "approver", f"[case reopened] {note.strip()}")
    log_event(con, case_id, approver.strip(), "case_reopened", {"note": note.strip()})
    return get_case(con, case_id)


def case_timeline(con, case_id: str) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT ts, actor, event, detail_json FROM case_events WHERE case_id=? ORDER BY id", (case_id,))]


# ------------------------------------------------------------------ traced steps (problem 14)
def trace_step(con, trace_id: str, step: str, component: str, input_: dict | None, output: dict | None, started: float,
               case_id: str | None = None) -> None:
    con.execute("INSERT INTO trace_steps (trace_id, case_id, step, component, input_json, output_json, ms, ts) VALUES (?,?,?,?,?,?,?,?)",
                (trace_id, case_id, step, component, json.dumps(input_, ensure_ascii=False, default=str),
                 json.dumps(output, ensure_ascii=False, default=str), int((time.time() - started) * 1000), _now()))
    con.commit()
