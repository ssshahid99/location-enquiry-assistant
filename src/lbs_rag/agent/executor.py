"""Executor: run the tools, collect findings, prepare drafts and handoffs. It never sends or applies anything.

Given the Planner's plan for a case message it:
  1. checks the customer's inputs with deterministic validators (coordinates, matrix size, waypoints, timestamps, response shape,
     required parameters)                                                                                     [problems 12]
  2. asks the RAG for the grounded, cited knowledge answer                                                    [problem 2]
  3. matches requirements to documented capabilities with citations, flagging gaps                           [problem 8]
  4. drafts a reply / discovery message / follow-up from facts, findings and history                          [problem 10]
  5. builds a structured handoff package                                                                      [problem 7]
  6. records the steps (trace), saves the checkpoint and stores the draft as a PENDING external_send approval [problem 14]
"""
import json
import re
import time
from functools import lru_cache

from ..retrieve.query_analyzer import detect
from ..store import cases
from ..tools import capabilities
from ..tools.request_splitter import split_matrix
from ..tools.validators import validate_latlng, validate_matrix, validate_pair, validate_waypoints

DRAFT_NOTE = "DRAFT: not sent. It becomes available to send only after an authorised approver approves it."


# ------------------------------------------------------------------------------------------------ deterministic validators
@lru_cache(maxsize=1)
def ola_required_params() -> dict[str, list[str]]:
    """{endpoint path: [required query/path params]} read from the fetched Ola OpenAPI spec chunks (the authoritative source)."""
    from ..index.bm25 import load_chunks
    out: dict[str, list[str]] = {}
    for c in load_chunks():
        if c["kind"] != "openapi" or not re.match(r"^(GET|POST|PUT|DELETE) /", c["section"]):
            continue
        path = c["section"].split(" ", 1)[1]
        out.setdefault(path, [])
        for m in re.finditer(r"^- ([A-Za-z_][\w-]*) \((?:query|path), required\)", c["text"], re.M):
            if m.group(1) not in out[path]:
                out[path].append(m.group(1))
    return out


_OLA_PATH_HINT = {"directions": "/routing/v1/directions", "distance matrix": "/routing/v1/distanceMatrix",
                  "reverse geocode": "reverse", "autocomplete": "autocomplete"}


def required_params_check(provider: str | None, apis: list[str], message: str) -> list[dict]:
    """Which documented required parameters are absent from the parameters the customer showed. Ola only: it is the one provider whose
    required-parameter list we hold from an authoritative machine-readable spec; for the others we say so instead of guessing."""
    given = {m.group(1).lower() for m in re.finditer(r"\b([A-Za-z_][\w-]*)=[^\s&,;]+", message)}
    given |= {m.lower() for m in re.findall(r'"([A-Za-z_][\w-]*)"\s*:', message)}
    if not given:
        return []
    if provider != "Ola Maps":
        return [{"check": "required_params", "ok": None,
                 "detail": f"No verified required-parameter list is held for {provider or 'this provider'}; compare against the vendor reference."}]
    spec = ola_required_params()
    out = []
    for api in apis or []:
        hint = _OLA_PATH_HINT.get(api.lower())
        if not hint:
            continue
        paths = [p for p in spec if (p == hint if hint.startswith("/") else hint in p.lower())]
        for p in paths:
            missing = [r for r in spec[p] if r.lower() not in given and r.lower() != "api_key"]
            out.append({"check": "required_params", "ok": not missing, "endpoint": p,
                        "detail": (f"{p}: required parameters not shown: {', '.join(missing)}" if missing
                                   else f"{p}: all documented required parameters are present")})
    return out


def check_inputs(message: str, *, provider: str | None = None, apis: list[str] | None = None, facts: dict | None = None) -> list[dict]:
    """Deterministic checks on what the customer showed. Each finding: {check, ok (True|False|None=unknown), detail}."""
    d = detect(message)
    provs = [provider] if provider else d["providers"]
    apis = apis or d["apis"]
    findings: list[dict] = []
    prov0 = provs[0] if provs else None

    for m in re.finditer(r"(-?\d{1,3}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})", message):
        raw = f"{m.group(1)},{m.group(2)}"
        issues = validate_pair(raw, prov0 or "Google Maps Platform")
        note = "" if prov0 else " (provider not stated; assumed lat,lng order)"
        findings.append({"check": "coordinates", "ok": not issues, "value": raw,
                         "detail": (f"{raw}: " + "; ".join(issues) + note) if issues else f"{raw}: valid{note}"})

    mm = re.search(r"(\d+)\s*[x×]\s*(\d+)", message)
    if mm and re.search(r"matri|origin|destination", message, re.I):
        o, de = int(mm.group(1)), int(mm.group(2))
        for p in (provs or ["Ola Maps", "Google Maps Platform", "Mapbox"]):
            if p not in ("Ola Maps", "Google Maps Platform", "Mapbox"):
                continue
            issues = validate_matrix(p, o, de, traffic=bool(re.search(r"traffic|departure_time", message, re.I)))
            if issues:
                plan = split_matrix(p, o, de)
                findings.append({"check": "matrix_size", "ok": False, "provider": p,
                                 "detail": f"{p} {o}x{de}: {'; '.join(issues)}. Split into {plan.requests} requests of up to "
                                           f"{plan.block_origins}x{plan.block_destinations}."})
            else:
                findings.append({"check": "matrix_size", "ok": True, "provider": p, "detail": f"{p} {o}x{de}: within the per-request limits"})

    wm = re.search(r"(\d+)\s*(?:intermediate\s+)?(?:waypoints|stops)", message, re.I)
    if wm:
        n = int(wm.group(1))
        basic = bool(re.search(r"\bbasic\b", message, re.I))
        for p in (provs or []):
            issues = validate_waypoints(p, "Directions Basic" if basic and p == "Ola Maps" else "Directions", n)
            findings.append({"check": "waypoints", "ok": not issues, "provider": p,
                             "detail": f"{p} {n} waypoints: " + ("; ".join(issues) if issues else "within the documented limit")})

    for m in re.finditer(r"\b(\d{1,2}[:.]\d{2})\s*(am|pm)?\s*(ist|utc|gmt|[a-z]{3,4})?\b", message, re.I):
        if not (m.group(3) and m.group(3).lower() in {"ist", "utc", "gmt", "pst", "est", "cet"}):
            findings.append({"check": "timestamp", "ok": False, "value": m.group(0).strip(),
                             "detail": f"time '{m.group(0).strip()}' has no timezone: ask for date and timezone (for example 2026-11-09 14:05 IST)"})
            break
    if re.search(r"\b(yesterday|today|this (morning|afternoon|evening)|last night)\b", message, re.I) and not re.search(r"\b\d{4}-\d{2}-\d{2}\b", message):
        findings.append({"check": "timestamp", "ok": False, "detail": "relative date only (yesterday/today/this morning): ask for the absolute date and time"})

    start = message.find("{")
    if start >= 0 and re.search(r'"[\w-]+"\s*:', message[start:]):           # looks like a pasted JSON body (even if truncated)
        end = message.rfind("}")
        candidate = message[start:end + 1] if end > start else message[start:]
        try:
            obj = json.loads(candidate)
            keys = sorted(obj.keys())[:8] if isinstance(obj, dict) else []
            has_err = any(k.lower() in {"status", "error", "error_message", "code", "message"} for k in keys)
            findings.append({"check": "response_shape", "ok": True,
                             "detail": f"valid JSON with top-level keys {keys}" + ("; contains an error/status field" if has_err else "")})
        except ValueError:
            findings.append({"check": "response_shape", "ok": False,
                             "detail": "the pasted response looks like JSON but is not valid (truncated or edited?): ask for the full raw body"})

    findings += required_params_check(prov0, apis, message)
    return findings


# ------------------------------------------------------------------------------------------------ drafting (templates; no model)
def _sources_block(citations: list[dict]) -> str:
    seen, lines = set(), []
    for c in citations:
        if c["n"] not in seen:
            seen.add(c["n"])
            lines.append(f"[{c['n']}] {c['provider']} - {c['doc_url']}")
    return "Sources:\n" + "\n".join(lines) if lines else ""


def draft_communication(kind: str, *, plan: dict, knowledge: dict | None = None, validations: list[dict] | None = None,
                        capability_matches: list[dict] | None = None, facts: dict | None = None) -> str:
    """kind: support_reply | discovery | follow_up. Built only from verified material: the grounded answer (with its sources),
    deterministic validation findings, documented capabilities (gaps labelled), and the Planner's questions."""
    parts = ["Hello,", ""]
    know_text = (knowledge or {}).get("text", "").strip()
    refused = (knowledge or {}).get("refused")
    if kind == "support_reply":
        parts.append("Thanks for the details. Here is what we found so far.")
    elif kind == "discovery":
        parts.append("Thanks for reaching out. Based on what you shared, here is how the options line up.")
    else:
        parts.append("A quick follow-up on where we are.")
    if kind == "discovery" and capability_matches:
        # Verified capability evidence replaces the model-written essay: the essay can contain unsupported claims and can contradict
        # the verified table. Providers are not ranked beyond what the documentation shows.
        parts += ["", "The documented capabilities relevant to your requirements are below, with sources. "
                      "We only state what the documentation shows; items marked [GAP] are not documented in the sources we hold and need vendor confirmation."]
        know_text = ""
    if know_text and not refused:
        parts += ["", know_text]
    bad = [v for v in (validations or []) if v.get("ok") is False]
    if bad:
        parts += ["", "Things to check in your request:"] + [f"- {v['detail']}" for v in bad]
    if capability_matches:
        parts += ["", capabilities.render_table(capability_matches)]
    qs = plan.get("questions") or []
    if qs:
        parts += ["", "To move forward we need:"] + [f"- {q['question']}" for q in qs]
    if plan.get("needs_human_followup"):
        parts += ["", "(A colleague will follow up personally on: " + ", ".join(plan["needs_human_followup"]) + ".)"]
    esc = plan.get("escalation") or {}
    if esc.get("recommend"):
        parts += ["", f"We are escalating this to {esc.get('owner')}; we are not promising any SLA, fix date or timeline in this message."]
    parts += ["", _sources_block((knowledge or {}).get("citations", [])), "", DRAFT_NOTE]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(p for p in parts)).strip()


# ------------------------------------------------------------------------------------------------ handoff
def build_handoff(con, case_id: str) -> dict:
    """Structured handoff package assembled from the case record (problem 7). The Critic checks it for completeness."""
    case = cases.get_case(con, case_id)
    if case is None:
        raise ValueError(f"case {case_id} not found")
    state = cases.load_state(con, case_id)
    ex = state.get("executor", {})
    history = cases.get_case_history(con, case_id, limit=200)
    asked = state.get("asked_counts", {})
    facts = cases.known_facts(con, case_id)
    return {
        "case_id": case_id, "customer": case["customer"], "workflow": case["workflow"], "category": case["category"],
        "urgency": case["urgency"], "provider": case["provider"], "api": case["api"], "owner": case["owner"], "status": case["status"],
        "requirements": {k: v for k, v in facts.items() if k in {"use_case", "api_needs", "volume", "regions", "budget", "timeline",
                                                                  "constraints", "decision_maker"}},
        "findings": {"known_facts": facts, "validations": ex.get("validations", []), "capabilities": ex.get("capabilities", []),
                     "knowledge_answer": ex.get("knowledge_text"), "knowledge_sources": ex.get("citations", [])},
        "attempts": [m["content"][:300] for m in history if m["role"] == "agent"][-5:],
        "open_questions": [k for k, n in asked.items() if k not in facts],
        "needs_human_followup": state.get("needs_human_followup", []),
        "next_action": case["next_action"], "pending_approvals": [a["id"] for a in cases.list_pending(con, case_id)],
        "messages": len(history),
    }


def render_handoff(pkg: dict) -> str:
    lines = [f"Handoff for case {pkg['case_id']} ({pkg['workflow']} / {pkg['category']}, urgency {pkg['urgency']})",
             f"Customer: {pkg['customer']} | Provider/API: {pkg['provider']} / {pkg['api']} | Owner: {pkg['owner'] or 'unassigned'} | Status: {pkg['status']}"]
    if pkg["requirements"]:
        lines += ["Requirements:"] + [f"- {k}: {v}" for k, v in pkg["requirements"].items()]
    f = pkg["findings"]
    lines += ["Findings:"] + [f"- {v['detail']}" for v in f["validations"] if v.get("ok") is False] or []
    if f.get("knowledge_answer"):
        lines.append(f"- Knowledge answer: {f['knowledge_answer'][:300]}")
    if pkg["open_questions"]:
        lines.append("Open questions: " + ", ".join(pkg["open_questions"]))
    if pkg["needs_human_followup"]:
        lines.append("Needs human follow-up on: " + ", ".join(pkg["needs_human_followup"]))
    lines.append(f"Next action: {pkg['next_action'] or 'not set'}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ tracker updates (problem 6)
TRACKED_FACTS = ("use_case", "api_needs", "volume", "regions", "budget", "timeline", "constraints", "decision_maker",
                 "error_response", "timestamp", "impact", "provider_api")


def extract_changes(con, case_id: str, plan: dict) -> dict:
    """What the conversation says that the APPROVED tracker does not yet say: {field: {old, new}}. Requirements, findings, category and
    urgency only; owner / status / next action are proposed by the Planner as owner_action."""
    tracker = cases.get_tracker(con, case_id)
    changes: dict[str, dict] = {}
    for k in TRACKED_FACTS:
        v = (plan.get("known_facts") or {}).get(k)
        if v and tracker.get(k) != v:
            changes[k] = {"old": tracker.get(k), "new": v}
    for k in ("category", "urgency"):
        v = plan.get(k)
        if v and tracker.get(k) != v:
            changes[k] = {"old": tracker.get(k), "new": v}
    return changes


def propose_tracker_update(con, case_id: str, plan: dict) -> int | None:
    """Propose (never apply) a tracker update for human approval; if one is already pending, extend it instead of adding another."""
    changes = extract_changes(con, case_id, plan)
    if not changes:
        return None
    pending = [a for a in cases.list_pending(con, case_id) if a["type"] == "tracker_update"]
    if pending:
        merged = {**pending[0]["payload"].get("changes", {}), **changes}
        cases.update_pending_payload(con, pending[0]["id"], {"changes": merged, "source": "executor"}, actor="executor")
        return pending[0]["id"]
    return cases.request_approval(con, case_id, "tracker_update", {"changes": changes, "source": "executor"}, requested_by="executor")


# ------------------------------------------------------------------------------------------------ orchestration
def run_executor(con, case_id: str, plan: dict, message: str, *, submit: bool = True, use_rag: bool = True) -> dict:
    """Run the Executor on a planned case message. Returns findings and a draft; stores them on the case; creates at most one PENDING
    external_send approval for the draft (never sends)."""
    if cases.get_case(con, case_id) is None:
        raise ValueError(f"case {case_id} not found")
    trace_id = (cases.load_state(con, case_id).get("last_trace_id")) or "exec"
    steps: dict[str, dict] = {}

    def timed(name, fn):
        t0 = time.time()
        res = fn()
        cases.trace_step(con, trace_id, name, "executor", {"message": message[:300]}, {"summary": str(res)[:400]}, t0, case_id=case_id)
        return res

    prov = (plan.get("service", {}).get("providers") or [None])[0]
    apis = plan.get("service", {}).get("apis") or []
    context = timed("context", lambda: cases.summarize_history(con, case_id))
    validations = timed("validate_inputs", lambda: check_inputs(message, provider=prov, apis=apis, facts=plan.get("known_facts")))

    caps: list[dict] = []
    if plan.get("workflow") == "sales" or plan.get("category") in ("how_to", "troubleshooting"):
        caps = timed("capability_match", lambda: capabilities.match_capabilities(message))

    knowledge: dict = {}
    if use_rag:
        from ..generate.answer import answer            # imported lazily: it loads the retrieval stack

        def ask():
            a = answer(message, persist=False)
            return {k: a.get(k) for k in ("text", "citations", "abstained", "refused", "escalate", "reason", "tool_calls")}
        knowledge = timed("rag_answer", ask)

    kind = ("discovery" if plan.get("workflow") == "sales" and plan.get("category") in ("discovery", "capability_match", "licensing", "competitive")
            else "support_reply" if plan.get("workflow") == "support" else "follow_up")
    draft = timed("draft", lambda: draft_communication(kind, plan=plan, knowledge=knowledge, validations=validations,
                                                       capability_matches=caps, facts=plan.get("known_facts")))

    state = cases.load_state(con, case_id)
    state["executor"] = {"validations": validations, "capabilities": caps, "knowledge_text": knowledge.get("text"),
                         "citations": knowledge.get("citations", []), "draft_kind": kind, "draft": draft}
    state["needs_human_followup"] = plan.get("needs_human_followup", [])
    state["step"] = "draft_ready"
    cases.save_state(con, case_id, state)
    cases.add_message(con, case_id, "agent", f"[draft prepared: {kind}; pending approval]")

    approval_id, tracker_id = None, None
    if submit:
        payload = {"to": "customer", "kind": kind, "draft": draft, "citations": knowledge.get("citations", [])}
        pending_send = [a for a in cases.list_pending(con, case_id) if a["type"] == "external_send"]
        if pending_send:                                   # a follow-up refreshes the draft that is still waiting; never a second request
            cases.update_pending_payload(con, pending_send[0]["id"], payload, actor="executor")
            approval_id = pending_send[0]["id"]
        else:
            approval_id = cases.request_approval(con, case_id, "external_send", payload, requested_by="executor")
        tracker_id = propose_tracker_update(con, case_id, plan)
    return {"case_id": case_id, "kind": kind, "context": context, "validations": validations, "capabilities": caps,
            "knowledge": knowledge, "draft": draft, "approval_id": approval_id, "tracker_approval_id": tracker_id}


# ------------------------------------------------------------------------------------------------ tool registry (for the orchestrator)
def tool_specs() -> dict[str, dict]:
    """Names and descriptions of the tools the Executor uses, for the orchestration layer and for documentation."""
    from ..generate.answer import answer
    from ..tools.pricing_intent import run as price_run
    return {
        "rag_answer": {"fn": lambda q: answer(q, persist=False), "doc": "Grounded, cited answer with abstention and guard (RAG)."},
        "validate_inputs": {"fn": check_inputs, "doc": "Coordinates, matrix size, waypoints, timestamps, response shape, required params."},
        "price_quote": {"fn": price_run, "doc": "Extract a workload and compute prices with the deterministic calculator."},
        "match_capabilities": {"fn": capabilities.match_capabilities, "doc": "Requirement -> documented capability per provider; gaps flagged."},
        "get_history": {"fn": cases.get_case_history, "doc": "Case messages."},
        "known_facts": {"fn": cases.known_facts, "doc": "Facts already established on the case (do not re-ask)."},
        "summarize_history": {"fn": cases.summarize_history, "doc": "Deterministic summary of facts and recent messages."},
        "build_handoff": {"fn": build_handoff, "doc": "Structured handoff package for the case."},
        "draft_communication": {"fn": draft_communication, "doc": "Reply / discovery / follow-up draft; never sent."},
    }
