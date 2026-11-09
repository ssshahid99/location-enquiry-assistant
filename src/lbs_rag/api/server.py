"""FastAPI server. Run: uvicorn lbs_rag.api.server:app --reload  (PYTHONPATH must include the project root and src).

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
from ..generate.answer import answer
from ..safety.pii import redact
from ..store import cases, handlers
from ..store import repositories as repo

app = FastAPI(title="Location Services Copilot", version="0.3")


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


def _db():
    return repo.connect()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/answer")
def ask(body: AskIn) -> dict:
    clean = redact(body.query)             # PII is removed before anything is stored or sent to the model
    return answer(clean, rewrite=body.rewrite)


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
