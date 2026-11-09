"""One review screen per case instead of 3-4 separate approvals (checklist A4).

What this does NOT change: every request is still decided individually (its own approval record, approver, note and timeline entry), one
authorised approver is enough, rejection needs a reason, an approved action is applied exactly once, and nothing is ever sent externally.
Nothing is approved automatically: `decide_bundle` only acts on the decisions a human explicitly passes in, for requests that human was shown.

Staleness: a pending request is STALE when the customer wrote again after it was created (the facts it was built on may have changed).
A stale request cannot be approved in a bundle unless the approver explicitly acknowledges it; rejecting it needs no acknowledgement.
"""
from . import cases, handlers

# A sensible reading order: who owns it and what changes first, the customer reply near the end, the handoff last.
ORDER = ["owner_action", "tracker_update", "escalation", "external_send", "handoff", "other"]


def _stale(con, case_id: str, created_at: str) -> bool:
    row = con.execute("SELECT 1 FROM messages WHERE case_id=? AND role='customer' AND ts>? LIMIT 1", (case_id, created_at)).fetchone()
    return row is not None


def review_case(con, case_id: str) -> dict:
    """Everything an approver needs on one screen: the case, each pending request (in reading order) with warnings, and a summary."""
    case = cases.get_case(con, case_id)
    if case is None:
        raise ValueError(f"case {case_id} not found")
    items = []
    for a in cases.list_pending(con, case_id):
        pl, warn = a["payload"], []
        crit = pl.get("critic") or {}
        stale = _stale(con, case_id, a["created_at"])
        if stale:
            warn.append("The customer wrote again after this request was prepared: re-check it before approving.")
        if crit and crit.get("verdict") not in (None, "pass"):
            warn.append(f"The Critic did not pass this draft ({crit.get('verdict')}).")
        if crit.get("handoff_ready") is False:
            warn.append("The handoff package is incomplete.")
        if a["type"] == "escalation" and pl.get("missing_evidence"):
            warn.append("Evidence still missing: " + ", ".join(pl["missing_evidence"]))
        items.append({**a, "stale": stale, "warnings": warn})
    items.sort(key=lambda a: (ORDER.index(a["type"]) if a["type"] in ORDER else len(ORDER), a["id"]))
    return {"case": case, "items": items, "count": len(items), "stale_count": sum(i["stale"] for i in items),
            "warning_count": sum(bool(i["warnings"]) for i in items)}


def decide_bundle(con, case_id: str, decisions: list[dict], *, approver: str, note: str | None = None) -> dict:
    """Apply the human's decisions to several requests of ONE case. Each decision is {action_id, approve, note?, edited_payload?,
    acknowledge_stale?}. Every request is decided and applied on its own: a failure on one is reported and does not undo or block the others.
    Returns {"results": [...], "done": n, "failed": n}."""
    if not decisions:
        raise ValueError("no decisions given")
    seen = set()
    for d in decisions:                                       # validate the whole bundle first: reject it as a unit if it is malformed
        aid = d.get("action_id")
        if aid in seen:
            raise ValueError(f"request {aid} appears twice in the bundle")
        seen.add(aid)
        a = cases.get_action(con, aid) if aid is not None else None
        if a is None or a["case_id"] != case_id:
            raise ValueError(f"request {aid} does not belong to case {case_id}")
    results = []
    for d in sorted(decisions, key=lambda d: _rank(con, d["action_id"])):
        aid, approve = d["action_id"], bool(d.get("approve"))
        n = d.get("note") or note
        try:
            a = cases.get_action(con, aid)
            if approve and a["status"] == "pending" and _stale(con, case_id, a["created_at"]) and not d.get("acknowledge_stale"):
                raise ValueError("this request is stale (the customer wrote again after it was prepared); acknowledge it explicitly to approve")
            if approve:
                res = handlers.approve_and_apply(con, aid, approver=approver, note=n, edited_payload=d.get("edited_payload"))
            else:
                res = handlers.reject(con, aid, approver=approver, note=n or "")
            results.append({"action_id": aid, "type": a["type"], "ok": True, "status": res["status"], "result": res.get("applied_result")})
        except ValueError as e:
            results.append({"action_id": aid, "type": (cases.get_action(con, aid) or {}).get("type"), "ok": False, "error": str(e)})
    cases.log_event(con, case_id, (approver or "").strip() or "unknown", "bundle_reviewed",
                    {"action_ids": [r["action_id"] for r in results], "ok": [r["action_id"] for r in results if r["ok"]]})
    return {"results": results, "done": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results)}


def _rank(con, action_id: int) -> tuple:
    a = cases.get_action(con, action_id)
    return (ORDER.index(a["type"]) if a["type"] in ORDER else len(ORDER), action_id)
