"""Typed tool registry (Activity 12). Every tool has a Pydantic input and output; every call is logged; an unregistered tool or an
invalid argument is refused and counted, never executed. Four tools match the gold tool vocabulary:

  doc_search       cited, grounded answer from the documentation (the RAG pipeline: hybrid retrieval, grounding check)
  calculator       deterministic price / rate-card arithmetic from config/pricing.yaml (no model arithmetic)
  update_record    PROPOSES a field-level diff on a sandbox ticket / opportunity; queued for human approval, never applied here
  escalate_ticket  PROPOSES an escalation (owner, severity, summary); queued for human approval, nothing is contacted

The `ctx` passed to a tool is a dict: {"con": sqlite connection, "conv_id": str}.
"""
from typing import Callable

from pydantic import BaseModel, Field, ValidationError

from ..store import cases
from . import sandbox


class ToolError(RuntimeError):
    pass


class DocSearchIn(BaseModel):
    query: str = Field(min_length=1)


class DocSearchOut(BaseModel):
    text: str
    citations: list[dict] = []
    retrieved: list[dict] = []
    tool_calls: list[dict] = []
    abstained: bool = False
    refused: bool = False
    reason: str | None = None
    model_calls: int = 0


class CalculatorIn(BaseModel):
    question: str = Field(min_length=1)


class CalculatorOut(BaseModel):
    results: list[dict] = []
    unverified: list[str] = []
    errors: list[str] = []
    assumptions: list[str] = []


class UpdateRecordIn(BaseModel):
    record_id: str
    fields: dict[str, str | int | float | bool | None]


class UpdateRecordOut(BaseModel):
    record_id: str
    mode: str = "propose"
    fields: dict
    diff: dict
    action_id: int
    status: str = "pending"


class EscalateIn(BaseModel):
    category: str
    owner: str
    severity: str = Field(pattern=r"^SEV[1-4]$")
    summary: str = Field(min_length=1)
    customer_query: str = ""
    draft_reply: str = ""
    collect: list[str] = []


class EscalateOut(BaseModel):
    action_id: int
    case_id: str | None = None
    status: str = "pending"


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[type[BaseModel], type[BaseModel], Callable]] = {}
        self.calls: list[dict] = []

    def register(self, name: str, inp: type[BaseModel], out: type[BaseModel]):
        def deco(fn):
            self._tools[name] = (inp, out, fn)
            return fn
        return deco

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, args: dict, ctx: dict) -> BaseModel:
        entry = {"tool": name, "args": args, "ok": False, "schema_ok": True, "error": None}
        self.calls.append(entry)
        if name not in self._tools:
            entry.update(schema_ok=False, error="unregistered tool")
            raise ToolError(f"tool '{name}' is not registered")
        inp, out, fn = self._tools[name]
        try:
            parsed = inp(**args)
        except ValidationError as e:
            entry.update(schema_ok=False, error=f"invalid arguments: {e.errors()[0]['msg']}")
            raise ToolError(entry["error"]) from e
        try:
            result = out.model_validate(fn(parsed, ctx))
        except ValidationError as e:
            entry.update(schema_ok=False, error="tool output failed its schema")
            raise ToolError(entry["error"]) from e
        except Exception as e:  # noqa: BLE001 - a failing tool must not crash the chat turn
            entry.update(error=f"{type(e).__name__}: {e}")
            raise ToolError(entry["error"]) from e
        entry["ok"] = True
        return result

    def log_external(self, name: str, args: dict, ok: bool = True) -> None:
        """Record a registered tool that ran inside another tool (the answer pipeline runs the calculator itself)."""
        self.calls.append({"tool": name, "args": args, "ok": ok, "schema_ok": True, "error": None, "inside": "doc_search"})


def build() -> Registry:
    reg = Registry()

    @reg.register("doc_search", DocSearchIn, DocSearchOut)
    def _doc_search(a: DocSearchIn, ctx: dict) -> dict:
        from ..generate.answer import answer
        r = answer(a.query, persist=False)
        return {"text": r["text"], "citations": r.get("citations", []), "retrieved": r.get("retrieved", []),
                "tool_calls": r.get("tool_calls", []), "abstained": bool(r.get("abstained")), "refused": bool(r.get("refused")),
                "reason": r.get("reason"), "model_calls": r.get("model_calls", 0)}

    @reg.register("calculator", CalculatorIn, CalculatorOut)
    def _calculator(a: CalculatorIn, ctx: dict) -> dict:
        from ..tools import pricing_intent
        p = pricing_intent.run(a.question)
        notes = sorted({n for r in p["results"] for n in (r.get("notes") or [])}) if p["results"] else []
        return {"results": p["results"], "unverified": p["unverified"], "errors": p["errors"], "assumptions": notes}

    @reg.register("update_record", UpdateRecordIn, UpdateRecordOut)
    def _update_record(a: UpdateRecordIn, ctx: dict) -> dict:
        try:
            prop = sandbox.propose(ctx["con"], a.record_id, a.fields)
        except sandbox.SandboxError as e:
            raise ToolError(str(e)) from e
        aid = cases.request_approval(ctx["con"], None, "record_update", {**prop, "conv_id": ctx.get("conv_id")}, requested_by="chat")
        return {**prop, "action_id": aid, "status": "pending"}

    @reg.register("escalate_ticket", EscalateIn, EscalateOut)
    def _escalate(a: EscalateIn, ctx: dict) -> dict:
        con = ctx["con"]
        urgent = a.category in ("production_incident", "security_incident", "billing_incident")
        cid = cases.create_case(con, workflow="support", type="incident", category=a.category, urgency="high" if urgent else "normal")
        cases.add_message(con, cid, "customer", a.customer_query)
        if a.draft_reply:
            cases.add_message(con, cid, "agent", a.draft_reply)
        aid = cases.request_approval(con, cid, "escalation", {
            "category": a.category, "owner": a.owner, "severity": a.severity, "summary": a.summary, "collect": a.collect,
            "customer_query": a.customer_query, "draft_reply": a.draft_reply, "conv_id": ctx.get("conv_id")}, requested_by="chat")
        return {"action_id": aid, "case_id": cid, "status": "pending"}

    return reg
