"""Orchestrator: one entry point that ties Router -> Planner -> Executor -> Critic -> approvals together, with pause and resume.

handle_message(con, message, case_id=None)
    refused  the guard's refusal text (no case)
    rag      the grounded answer (no case)
    agent    case: Planner (classify, gaps, escalation, proposals) -> Executor (validators, RAG, capabilities, draft, tracker proposal)
             -> Critic (verify / repair / needs_human). Everything consequential is a PENDING approval.
    human    case: Planner + handoff package + escalation proposal; no customer-facing draft
reply(con, case_id, message)        a customer reply on an open case (resume)
prepare_handoff(con, case_id)       build the handoff package and queue it as a pending `handoff` approval

Pause / resume: the case state (checkpoint) records where the case stopped; a reply re-plans with everything already known, refreshes the
pending draft and tracker proposal instead of creating duplicates, and a rejected approval's reason is in the history for the next pass.
The orchestrator never sends or applies anything: approval (one authorised human) is the only way forward (store/handlers.py).
"""
import time

from config.settings import count_model_calls

from ..generate.answer import answer
from ..safety.pii import redact_with_report
from ..store import cases, customers
from . import critic, executor, planner, router


def _approvals(con, case_id: str) -> list[dict]:
    return [{"id": a["id"], "type": a["type"], "requested_by": a["requested_by"]} for a in cases.list_pending(con, case_id)]


def _next_step(state: dict, pl: dict, crit: dict | None, route_name: str) -> str:
    if route_name == "human":
        return "Waiting for a person: review the handoff and the escalation proposal in the Approvals tab."
    if pl.get("questions"):
        return f"Waiting for the customer: {len(pl['questions'])} question(s) to send (the draft is pending approval)."
    if crit and crit["verdict"] == "needs_human":
        return "A person must review the draft: the Critic found issues it cannot repair."
    return "The draft and proposals are pending approval; nothing has been sent."


def prepare_handoff(con, case_id: str) -> dict:
    """Build the handoff package, check it with the Critic, and queue it as ONE pending `handoff` approval (refreshed if already pending)."""
    pkg = executor.build_handoff(con, case_id)
    last_plan = cases.load_state(con, case_id).get("last_plan", {"missing_required": []})
    pending = _approvals(con, case_id)
    issues = critic.check_handoff(pkg, last_plan, pending_types=[a["type"] for a in pending])
    payload = {"package": pkg, "text": executor.render_handoff(pkg), "to_role": pkg.get("category") or "next owner",
               "assign_to": None, "handoff_ready": not issues, "handoff_issues": [{k: i[k] for k in ("check", "severity", "detail")} for i in issues]}
    existing = [a for a in cases.list_pending(con, case_id) if a["type"] == "handoff"]
    if existing:
        cases.update_pending_payload(con, existing[0]["id"], payload, actor="orchestrator")
        aid = existing[0]["id"]
    else:
        aid = cases.request_approval(con, case_id, "handoff", payload, requested_by="orchestrator")
    return {"approval_id": aid, "handoff_ready": not issues, "issues": issues, "text": payload["text"]}


def handle_message(con, message: str, **kw) -> dict:
    """Route and handle one message. The result carries `model_calls` (measured) and `elapsed_ms`."""
    with count_model_calls() as calls:
        out = _handle(con, message, **kw)
    out["model_calls"] = calls.count
    return out


def _handle(con, message: str, *, case_id: str | None = None, customer: str = "unknown", submit: bool = True,
            use_rag: bool = True) -> dict:
    t0 = time.time()
    clean, found = redact_with_report(message)                 # personal data / secrets are removed before anything is stored or analysed
    case = cases.get_case(con, case_id) if case_id else None
    if case_id and case is None:
        raise ValueError(f"case {case_id} not found")
    if case and case["status"] == "closed":
        raise ValueError(f"case {case_id} is closed; an authorised approver must reopen it first")
    decision = router.route(clean, case_open=case is not None)
    out = {"route": decision["route"], "reason": decision["reason"], "signals": decision["signals"], "redacted": sorted(set(found)),
           "case_id": case_id}

    if decision["route"] in ("refused", "rag"):
        a = answer(clean, persist=submit)                       # the answer pipeline owns refusals, general and simple answers
        out.update({"text": a["text"], "citations": a.get("citations", []), "abstained": a.get("abstained"), "refused": a.get("refused"),
                    "escalate": a.get("escalate"), "case_id": a.get("case_id", case_id), "trace_id": a.get("trace_id")})
        if decision["route"] == "refused" and not a.get("refused"):
            out["route"] = "rag"                                 # defence in depth: the pipeline allowed it, so it is an ordinary answer
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out

    pl_pre = decision["plan"]
    esc = pl_pre["escalation"]
    cid = case_id or cases.create_case(con, customer=customer, workflow=pl_pre["workflow"],
                                       type="incident" if esc["recommend"] else "case", category=pl_pre["category"], urgency=pl_pre["urgency"])
    if case_id is None and submit:
        customers.attach_prior_facts(con, cid)                   # earlier setup facts of this customer: suggested, never assumed
    pl = planner.plan_case(con, cid, clean, submit=submit)
    out.update({"prior_facts": customers.pending_prior_facts(con, cid), "case_id": cid, "workflow": pl["workflow"], "category": pl["category"], "urgency": pl["urgency"],
                "questions": pl["questions"], "needs_human_followup": pl["needs_human_followup"],
                "ready_to_proceed": pl["ready_to_proceed"], "escalation": pl["escalation"]})

    if decision["route"] == "human":
        ho = prepare_handoff(con, cid) if submit else None
        out.update({"draft": None, "critic": None, "handoff": ho})
        cases.update_case(con, cid, actor="orchestrator", status="needs_human")      # a person must decide, whatever else is pending
        crit = None
    else:
        ex = executor.run_executor(con, cid, pl, clean, submit=submit, use_rag=use_rag)
        crit = critic.critique_case(con, cid, ex, pl) if submit else critic.critique_and_revise(ex, pl)
        out.update({"validations": ex["validations"], "capabilities": ex["capabilities"], "draft": crit["result"]["draft"],
                    "draft_kind": ex["kind"], "knowledge": ex["knowledge"],
                    "critic": {"verdict": crit["verdict"], "revisions": crit["revisions"], "issues": crit["issues"],
                               "handoff_ready": crit["handoff_ready"], "handoff_issues": crit["handoff_issues"]}})
    state = cases.load_state(con, cid)
    out.update({"approvals": _approvals(con, cid), "status": cases.get_case(con, cid)["status"],
                "next_step": _next_step(state, pl, crit, decision["route"]), "elapsed_ms": int((time.time() - t0) * 1000)})
    cases.log_event(con, cid, "orchestrator", "message_handled", {"route": decision["route"], "reason": decision["reason"]})
    return out


def reply(con, case_id: str, message: str, **kw) -> dict:
    """A customer reply on an open case: re-plan with all known facts (nothing already known is asked again) and refresh the pending draft."""
    return handle_message(con, message, case_id=case_id, **kw)
