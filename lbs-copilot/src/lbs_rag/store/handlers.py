"""What 'approved' DOES. Only internal, reversible updates happen here; this module never sends anything externally
(email, messages, vendor contact). External sends stay drafts that a person sends by hand.

Per action type (all go through one authorised human, see cases.decide):
  escalation       case -> escalated, owner = approver, next action = hand off to the provider team
  external_send    case -> reply_approved; the approved draft is recorded and a PERSON sends it (the system never does)
  handoff          case -> handed_off; owner = payload.assign_to (the approver may edit it) or the approver
  tracker_update   the approved changes are written to the tracker (case_tracker); owner / next_action also update the case
  owner_action     owner and next action are set on the case
"""
from . import cases

_TRACKER_TO_CASE = {"owner", "next_action"}


def _handler_for(con, action: dict):
    def handle(payload: dict) -> dict:
        kind, cid, approver = action["type"], action["case_id"], action["approver"]
        if kind == "escalation":
            if cid:
                cases.update_case(con, cid, actor=approver, status="escalated", owner=approver,
                                  next_action=f"Hand off to {payload.get('owner', 'the provider support team')}; "
                                              f"customer to provide: {', '.join(payload.get('collect', [])) or 'n/a'}")
            return {"applied": "case marked escalated; the reply remains a DRAFT for a person to send"}
        if kind == "external_send":
            if cid:
                cases.update_case(con, cid, actor=approver, status="reply_approved",
                                  next_action="Send the approved reply by hand (the system never sends messages)")
                cases.add_message(con, cid, "approver", f"[approved reply recorded; to be sent by a person] {str(payload.get('draft', ''))[:500]}")
            return {"applied": "approved; nothing was sent externally. A person must send the reply"}
        if kind == "handoff":
            assignee = payload.get("assign_to") or approver
            if cid:
                cases.update_case(con, cid, actor=approver, status="handed_off", owner=assignee,
                                  next_action=f"Handoff accepted ({payload.get('to_role', 'next owner')}): review the handoff package")
            return {"applied": f"case handed off to {assignee}"}
        if kind == "tracker_update":
            changes = payload.get("changes") or {}
            applied = {}
            for field, ch in changes.items():
                value = ch["new"] if isinstance(ch, dict) else ch
                if cid:
                    cases.set_tracker(con, cid, field, value, approved_by=approver, source_action_id=action["id"])
                    if field in _TRACKER_TO_CASE:
                        cases.update_case(con, cid, actor=approver, **{field: str(value)})
                applied[field] = value
            return {"applied_fields": applied}
        if kind == "owner_action":
            fields = {k: v for k, v in {"owner": payload.get("owner") or payload.get("assign_to"), "next_action": payload.get("next_action")}.items() if v}
            if cid and fields:
                cases.update_case(con, cid, actor=approver, **fields)
            return {"applied_fields": fields}
        return {"applied": "recorded only; nothing was sent externally"}
    return handle


def approve_and_apply(con, action_id: int, *, approver: str, note: str | None = None, edited_payload: dict | None = None) -> dict:
    """One authorised human approves (optionally editing the draft); the internal update is then applied exactly once."""
    decided = cases.decide(con, action_id, approve=True, approver=approver, note=note, edited_payload=edited_payload)
    return cases.apply_approved(con, action_id, _handler_for(con, decided))


def reject(con, action_id: int, *, approver: str, note: str) -> dict:
    """Reject with a reason. The reason is added to the case history so the Planner/Executor can use it on the next pass."""
    decided = cases.decide(con, action_id, approve=False, approver=approver, note=note)
    if decided["case_id"]:
        cases.add_message(con, decided["case_id"], "approver", f"[rejected {decided['type']}] {note}")
    return decided
