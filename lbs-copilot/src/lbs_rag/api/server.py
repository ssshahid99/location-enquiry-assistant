"""FastAPI server. Run: uvicorn lbs_rag.api.server:app --reload  (PYTHONPATH must include the project root and src).

/chat                      the chatbot: the Planner picks ONE route (llm | rag | escalation) per message; returns the 7 schema-valid outputs
/chat/{conversation_id}    the saved conversation state (history, known facts, questions asked, summary)
/actions/log               every human decision: reviewer, time, approve/reject, final content and applied diff
/sandbox/records/{id}      a sandbox ticket / opportunity (only written after a person approves a proposed update)
/answer                    direct grounded answer (RAG)
/cases/message             the router + agent: simple -> answer, incident / missing info / sales -> case, contract / security -> person
/cases/{id}/reply          a customer reply on an open case (resume)
/cases/{id}/handoff        build the handoff package and queue it for approval
/actions/pending, /actions/{id}/decide   one authorised approver (config/approvers.yaml) decides; approving applies an INTERNAL update only

Human approval is mandatory for consequential actions. Nothing here sends anything externally.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..agent import orchestrator
from ..chat import sandbox
from ..chat import state as conv_state
from ..chat.chat import chat as chat_turn
from ..generate.answer import answer
from ..safety.pii import redact
from ..store import cases, customers, handlers, review, traces
from ..store import repositories as repo

app = FastAPI(title="Location Services Copilot", version="0.5")


class AskIn(BaseModel):
    query: str = Field(min_length=3, max_length=4000)
    rewrite: bool = True    # 60-question A/B on the rebuilt corpus: citation support 74% -> 90%, fewer uncited answers


class MessageIn(BaseModel):
    message: str = Field(min_length=3, max_length=6000)
    customer: str = "unknown"


class DecideIn(BaseModel):
    approve: bool
    approver: str = Field(min_length=1)
    note: str | None = None                    # required when rejecting
    edited_payload: dict | None = None         # approver may edit the draft before approving


class BundleItemIn(BaseModel):
    action_id: int
    approve: bool
    note: str | None = None
    edited_payload: dict | None = None
    acknowledge_stale: bool = False            # needed to approve a request the customer has written after


class BundleIn(BaseModel):
    approver: str = Field(min_length=1)
    decisions: list[BundleItemIn] = Field(min_length=1)


def _db():
    return repo.connect()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/answer")
def ask(body: AskIn) -> dict:
    clean = redact(body.query)             # PII is removed before anything is stored or sent to the model
    return answer(clean, rewrite=body.rewrite)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    conversation_id: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    customer: str = Field(default="unknown", max_length=120)


@app.post("/chat")
def chat_message(body: ChatIn) -> dict:
    """One chat turn. Escalations and sandbox record updates are only PROPOSED (pending in /actions/pending); nothing is sent or changed."""
    con = _db()
    try:
        r = chat_turn(con, body.message, body.conversation_id, customer=body.customer)
    finally:
        con.close()
    keep = ("conv_id", "trace_id", "route", "route_reason", "route_source", "text", "outputs", "schema_errors", "citations", "tool_calls",
            "model_calls", "new_facts", "action_id", "case_id", "record_action_id")
    return {k: r[k] for k in keep if k in r}


@app.get("/chat/{conversation_id}")
def chat_state(conversation_id: str) -> dict:
    con = _db()
    try:
        if not conv_state.exists(con, conversation_id):
            raise HTTPException(status_code=404, detail="conversation not found")
        return conv_state.load(con, conversation_id)
    finally:
        con.close()


@app.get("/actions/log")
def actions_log(limit: int = 100) -> list[dict]:
    con = _db()
    try:
        return cases.decision_log(con, limit=min(max(limit, 1), 500))
    finally:
        con.close()


@app.get("/sandbox/records/{record_id}")
def sandbox_record(record_id: str) -> dict:
    con = _db()
    try:
        if not sandbox.SANDBOX_ID.match(record_id):
            raise HTTPException(status_code=404, detail="not a sandbox record id")
        return {"record_id": record_id, "fields": sandbox.get(con, record_id)}
    finally:
        con.close()


@app.get("/approvers")
def approvers() -> dict:
    return {"approvers": cases.approver_names(), "required_approvals": 1}


@app.post("/cases/message")
def case_message(body: MessageIn) -> dict:
    con = _db()
    try:
        return orchestrator.handle_message(con, body.message, customer=body.customer)
    finally:
        con.close()


@app.post("/cases/{case_id}/reply")
def case_reply(case_id: str, body: MessageIn) -> dict:
    con = _db()
    try:
        return orchestrator.reply(con, case_id, body.message)
    except ValueError as e:
        raise HTTPException(status_code=409 if "closed" in str(e) else 404, detail=str(e)) from e
    finally:
        con.close()


class CloseIn(BaseModel):
    approver: str = Field(min_length=1)
    note: str = ""


@app.post("/cases/{case_id}/close")
def case_close(case_id: str, body: CloseIn) -> dict:
    con = _db()
    try:
        return cases.close_case(con, case_id, approver=body.approver, note=body.note)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e) else 409, detail=str(e)) from e
    finally:
        con.close()


@app.post("/cases/{case_id}/reopen")
def case_reopen(case_id: str, body: CloseIn) -> dict:
    con = _db()
    try:
        return cases.reopen_case(con, case_id, approver=body.approver, note=body.note)
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e) else 409, detail=str(e)) from e
    finally:
        con.close()


@app.post("/cases/{case_id}/handoff")
def case_handoff(case_id: str) -> dict:
    con = _db()
    try:
        if cases.get_case(con, case_id) is None:
            raise HTTPException(status_code=404, detail="case not found")
        return orchestrator.prepare_handoff(con, case_id)
    finally:
        con.close()


@app.get("/cases")
def list_cases(status: str | None = None) -> list[dict]:
    con = _db()
    try:
        return cases.list_cases(con, status=status)
    finally:
        con.close()


@app.get("/actions/pending")
def pending() -> list[dict]:
    con = _db()
    try:
        return repo.list_pending(con)
    finally:
        con.close()


@app.get("/cases/{case_id}")
def case(case_id: str) -> dict:
    con = _db()
    try:
        c = cases.get_case(con, case_id)
        if c is None:
            raise HTTPException(status_code=404, detail="case not found")
        return {"case": c, "history": cases.get_case_history(con, case_id), "facts": cases.known_facts(con, case_id),
                "tracker": cases.get_tracker(con, case_id), "timeline": cases.case_timeline(con, case_id)}
    finally:
        con.close()


@app.get("/traces")
def list_traces(only: str | None = None, q: str | None = None, limit: int = 50) -> list[dict]:
    con = _db()
    try:
        try:
            return traces.list_traces(con, only=only, query_contains=q, limit=min(max(limit, 1), 500))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    finally:
        con.close()


@app.get("/traces/{trace_id}")
def get_trace(trace_id: str, full: bool = False) -> dict:
    """The steps and sources behind one answer. full=true adds the whole text of every retrieved passage (for failure investigation)."""
    con = _db()
    try:
        t = traces.export_trace(con, trace_id) if full else traces.get_trace(con, trace_id)
        if t is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return t
    except ValueError:
        raise HTTPException(status_code=404, detail="trace not found")
    finally:
        con.close()


@app.get("/cases/{case_id}/trace")
def case_trace(case_id: str) -> dict:
    con = _db()
    try:
        if cases.get_case(con, case_id) is None:
            raise HTTPException(status_code=404, detail="case not found")
        return traces.case_trace(con, case_id)
    finally:
        con.close()


class PriorFactIn(BaseModel):
    confirm: bool
    by: str = Field(min_length=1)
    value: str | None = None                   # a corrected value when confirming


@app.get("/cases/{case_id}/customer")
def case_customer(case_id: str) -> dict:
    """Earlier cases of the same customer and earlier facts still waiting for confirmation (never assumed)."""
    con = _db()
    try:
        if cases.get_case(con, case_id) is None:
            raise HTTPException(status_code=404, detail="case not found")
        return customers.summary(con, case_id)
    finally:
        con.close()


@app.post("/cases/{case_id}/customer/facts/{key}")
def case_customer_fact(case_id: str, key: str, body: PriorFactIn) -> dict:
    con = _db()
    try:
        fn = customers.confirm_prior_fact if body.confirm else customers.dismiss_prior_fact
        return fn(con, case_id, key, body.by, **({"value": body.value} if body.confirm else {}))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        con.close()


@app.get("/cases/{case_id}/review")
def case_review(case_id: str) -> dict:
    """All pending requests of one case on one screen, with warnings (stale, Critic not passed, incomplete handoff)."""
    con = _db()
    try:
        return review.review_case(con, case_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    finally:
        con.close()


@app.post("/cases/{case_id}/review/decide")
def case_review_decide(case_id: str, body: BundleIn) -> dict:
    """Decide several requests of one case at once. Each is still decided and applied individually; nothing is sent externally."""
    con = _db()
    try:
        return review.decide_bundle(con, case_id, [d.model_dump() for d in body.decisions], approver=body.approver)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        con.close()


@app.post("/actions/{action_id}/decide")
def decide(action_id: int, body: DecideIn) -> dict:
    con = _db()
    try:
        if body.approve:
            res = handlers.approve_and_apply(con, action_id, approver=body.approver, note=body.note, edited_payload=body.edited_payload)
        else:
            res = handlers.reject(con, action_id, approver=body.approver, note=body.note or "")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    finally:
        con.close()
    return {"id": action_id, "status": res["status"], "applied_at": res.get("applied_at"),
            "note": "internal update applied; nothing was sent externally" if body.approve else "rejected and recorded"}
