"""chat(con, message, conv_id) -> one turn of the chatbot.

message -> PII redaction -> conversation state -> Planner (ONE route) -> route handler -> response drafter (7 schema-valid outputs) -> trace.
Nothing consequential ever happens here: escalations and sandbox record updates are only PROPOSED (pending human approval, see the Approvals tab).
"""
import re
import uuid

from config.settings import count_model_calls, openai_client, settings

from ..agent import planner as case_rules
from ..generate import meta
from ..generate.guard import off_topic_message
from ..index.bm25 import load_chunks
from ..safety import escalation, escalation_policy
from ..safety.pii import PUBLIC_EMAILS, redact_with_report
from ..store import repositories as repo
from . import drafter, planner, registry, state

_NUMBERY = re.compile(r"[₹$€]\s?\d|\b\d[\d,.]*\s?(?:requests?|calls?|elements?|pairs|per\s+1,?000|rpm|qps|inr|usd)\b", re.I)
_SAFE_LLM = ("I'd rather check that in the documentation than guess. Could you tell me which provider and API you mean "
             "(for example Ola Directions or Google Distance Matrix) and what you need to know?")
_LLM_SYSTEM = """You are the chat front desk of a sales and support copilot for location APIs (Google Maps Platform, Mapbox, HERE, Ola Maps, \
Mappls, OSM Nominatim, OSRM). In this turn you only CONVERSE: reply briefly and politely, use the conversation summary for context, and ask \
for the detail you need. You must NOT state any price, limit, quota, parameter, error cause or capability: those come from the documents in \
a different step. If the customer needs such a fact, say you will look it up and ask what is missing. Never promise anything. Plain text, at most 3 sentences."""


def _chunk_text() -> dict[str, str]:
    global _CHUNKS
    if _CHUNKS is None:
        _CHUNKS = {c["chunk_id"]: c["text"] for c in load_chunks()}
    return _CHUNKS


_CHUNKS: dict[str, str] | None = None


def _llm_reply(message: str, st: dict, client=None) -> str:
    hist = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in st["history"][-6:])
    try:
        c = client or openai_client()
        r = c.chat.completions.create(model=settings.chat_model, temperature=0, max_tokens=180, messages=[
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": f"Conversation summary: {st['summary'] or '(none)'}\nRecent turns:\n{hist or '(none)'}\n\nCustomer: {message}\n/no_think"}])
        text = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
    except Exception:  # noqa: BLE001
        return _SAFE_LLM
    return _SAFE_LLM if (not text or _NUMBERY.search(text)) else text


def _format_diff(prop: dict) -> str:
    lines = [f"- {k}: {v['old'] if v['old'] is not None else '(empty)'} -> {v['new']}" for k, v in prop["diff"].items()] or ["- (no field changes)"]
    return f"\n\nProposed update to {prop['record_id']} (sandbox, pending human approval; nothing is changed until a person approves):\n" + "\n".join(lines)


def chat(con, message: str, conv_id: str | None = None, *, customer: str = "unknown", reg: registry.Registry | None = None,
         client=None, persist: bool = True, use_model: bool = True) -> dict:
    reg = reg or registry.build()
    conv_id = state.create(con, customer, conv_id)
    message, _pii = redact_with_report(message, allow_emails=PUBLIC_EMAILS)         # nothing raw is stored or sent onwards
    trace_id = uuid.uuid4().hex[:12]
    ctx = {"con": con, "conv_id": conv_id}
    with count_model_calls() as calls:
        st = state.load(con, conv_id)
        p = planner.plan(message, st, client=client, use_model=use_model)
        route = p["route"]
        cls = case_rules.classify(message, known_workflow=None)
        new_facts = case_rules.extract_facts(message)
        state.add_message(con, conv_id, "customer", message, route)
        changed = state.set_facts(con, conv_id, new_facts)
        state.mark_answered(con, conv_id, [k for k in new_facts if k in st["asked"]])
        facts = state.known_facts(con, conv_id)
        asked = state.asked(con, conv_id)
        text, citations, calc, refused, refusal_reason, esc_category, record, esc_summary = "", [], None, False, None, None, None, ""
        extra: dict = {}
        if route == "refuse":
            text, refused, refusal_reason = _refusal_text(message), True, p["reason"]
        elif route == "llm":
            kind = meta.classify(message)
            text = meta.respond(message)[0] if kind else _llm_reply(message, st, client)
        elif route == "rag":
            res = reg.call("doc_search", {"query": p["query"]}, ctx)
            text, citations, refused = res.text, res.citations, res.refused
            refusal_reason = res.reason if res.refused else None
            if res.refused:
                text = res.text
            for tc in res.tool_calls:
                if tc.get("tool") in ("pricing_calc", "rate_card", "free_tier"):
                    reg.log_external("calculator", {"question": p["query"]})
                    calc = {"results": tc.get("output", []), "assumptions": []}
                    break
            extra["abstained"] = res.abstained
        else:                                                                    # escalation
            esc = escalation.check(message)
            esc_category = esc["category"] or p["signals"].get("category") or "human_request"
            a = escalation_policy.assess(message, esc_category)
            if esc["escalate"]:
                res = reg.call("doc_search", {"query": message}, ctx)             # grounded facts; the pipeline adds the handoff wording
                body, citations = res.text, res.citations
            else:
                body = (f"I'll hand this to {a['owner']}. Please share the details they will need (what happened, when, and the request or error "
                        "if any) so they can act without coming back to you.")
            text = body
            esc_summary = message[:300]
            r = reg.call("escalate_ticket", {"category": esc_category, "owner": a["owner"], "severity": a["severity"], "summary": esc_summary,
                                             "customer_query": message, "draft_reply": body, "collect": esc.get("collect", [])}, ctx)
            text += (f"\n\nThis is queued for human approval as {a['severity']} for {a['owner']} (action #{r.action_id}); nothing is sent or "
                     "contacted until a person approves it.")
            extra["action_id"] = r.action_id
            extra["case_id"] = r.case_id
        if route in ("rag", "llm") and p["extra_tools"]:                         # sandbox record update: a proposal only
            rid = re.search(r"\b(?:TKT|OPP)-\d{3,8}\b", message).group(0)
            rl = drafter.role(message, route, None, cls.get("workflow", "support"), cls.get("category"))
            try:
                prop = reg.call("update_record", {"record_id": rid, "fields": drafter.record_fields(message, cls, rl)}, ctx)
                record = {"record_id": prop.record_id, "fields": prop.fields, "diff": prop.diff}
                text += _format_diff(record)
                extra["record_action_id"] = prop.action_id
            except registry.ToolError as e:
                text += f"\n\n(No record update was proposed: {e})"
        clar = drafter.clarifying(cls.get("workflow", "support"), cls.get("category"), facts, asked, route) if route != "refuse" else \
            {"needed": False, "questions": []}
        if clar["needed"] and route == "rag" and not extra.get("abstained"):
            text += "\n\nTo tailor this, could you tell me:\n" + "\n".join(f"{i}. {q['text']}" for i, q in enumerate(clar["questions"], 1))
            for q in clar["questions"]:
                state.mark_asked(con, conv_id, q["field"], q["text"])
            state.set_pending(con, conv_id, {"question": p["query"]})
        elif asked and not clar["needed"] and st.get("pending") and route == "rag":
            state.set_pending(con, conv_id, None)
        outputs = drafter.build(message=message, route=route, cls=cls, facts=facts, asked=asked, text=text, citations=citations,
                                chunk_text=_chunk_text(), calc=calc, esc_category=esc_category, refused=refused, refusal_reason=refusal_reason,
                                record=record, esc_summary=esc_summary, clar=clar)
        errors = drafter.validate(outputs)
        state.add_message(con, conv_id, "assistant", text, route)
        state.refresh_summary(con, conv_id)
    out = {"conv_id": conv_id, "trace_id": trace_id, "route": route, "route_reason": p["reason"], "route_source": p["source"], "text": text,
           "outputs": outputs, "schema_errors": errors, "tool_calls": reg.calls, "citations": citations, "model_calls": calls.count, "new_facts": changed, **extra}
    if persist:
        repo.save_trace(con, trace_id=trace_id, query=message, model=settings.chat_model, a={
            "abstained": bool(refused or extra.get("abstained")), "escalate": route == "escalation", "text": text,
            "reason": f"route:{route} ({p['source']}): {p['reason']}", "case_id": extra.get("case_id"), "retrieved": [],
            "tool_calls": reg.calls, "citations": citations})
    return out


def _refusal_text(message: str) -> str:
    from ..generate import guard
    g = guard.check(message)
    return g["message"] if g.get("refuse") else off_topic_message()
