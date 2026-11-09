"""Critic: review the Executor's draft and handoff before a human sees them (project problems 3, 7, 8). Mostly code, no model.

Checks (each issue has a severity and a fix hint):
  grounding        every cited sentence is supported by the passages it cites (numbers, provider, content overlap)
  numbers          no number in the draft lacks a source (cited passages, calculator, validators, capability evidence, known facts)
  commitments      no SLA / uptime / refund / discount / fix-date / timeline promise
  safety           no secret or personal data in the draft
  validations      every failed deterministic validation is mentioned to the customer
  capability       documented rows have a verified source; a [GAP] is never written up as "unsupported"
  handoff          problem summary, requirements, findings, owner and next action present; unresolved questions listed explicitly

Verdict: pass | revise | needs_human. `critique_and_revise` repairs what can be repaired deterministically (drops unsupported or
committing sentences and re-drafts), at most `MAX_REVISIONS` times; anything left becomes needs_human. The Critic never sends or applies.
"""
import re
from functools import lru_cache

from ..generate import grounding
from ..safety.pii import PUBLIC_EMAILS, redact_with_report
from ..store import cases
from . import executor as ex

MAX_REVISIONS = 2

_COMMIT = re.compile(r"\b(guarantee[sd]?|promise[sd]?|assure[sd]?|commit(?:ted)? to|will be (?:fixed|resolved|restored)|"
                     r"within \d+\s*(?:hours?|days?|minutes?)|by (?:tomorrow|end of (?:day|week))|99(?:\.\d+)?\s?%|sla of|"
                     r"full refund|discount of|we will (?:refund|credit|waive))\b", re.I)
_NEGATED = re.compile(r"\b(?:not|no|never|without|cannot|can't|won't|aren'?t)\b.{0,40}\b(?:promis\w*|guarantee\w*|sla|timeline|fix date|refund|discount)\b", re.I)
_DENIAL = re.compile(r"\b(?:does not|doesn'?t|cannot|can'?t|no support|not support(?:ed)?|unsupported|lacks?)\b", re.I)
_URL = re.compile(r"https?://\S+")
_MARK = re.compile(r"\[(\d+)\]")


@lru_cache(maxsize=1)
def _corpus() -> dict[str, str]:
    from ..index.bm25 import load_chunks
    return {c["chunk_id"]: c["text"] for c in load_chunks()}


def _issue(check: str, severity: str, detail: str, fix: str = "ask_human", sentence: str | None = None) -> dict:
    return {"check": check, "severity": severity, "detail": detail, "fix": fix, "sentence": sentence}


def _sentences(text: str) -> list[str]:
    text = grounding.normalize_markers(text)          # a citation after the full stop must stay attached to its sentence
    return [s for line in text.split("\n") for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]


def _hits_for(citations: list[dict], chunks: dict[str, str]) -> dict[int, dict]:
    return {c["n"]: {"text": chunks.get(c.get("chunk_id"), ""), "provider": c.get("provider", "")} for c in citations}


# ------------------------------------------------------------------------------------------------ individual checks
def check_grounding(knowledge: dict, chunks: dict[str, str], allowed: set[float]) -> list[dict]:
    text, cites = (knowledge or {}).get("text", ""), (knowledge or {}).get("citations", [])
    if not text or not cites:
        return []
    by_n = _hits_for(cites, chunks)
    out = []
    for s in _sentences(text):
        marks = [int(m) for m in _MARK.findall(s)]
        used = [by_n[n] for n in marks if n in by_n and by_n[n]["text"]]
        if marks and used and not grounding.sentence_supported(s, used, allowed):
            out.append(_issue("grounding", "high", "sentence is not supported by the passages it cites", "remove", s.strip()))
    return out


def _price_block_numbers(text: str) -> set[float]:
    nums: set[float] = set()
    in_block = False
    for line in text.split("\n"):
        if re.match(r"\s*(?:Price summary|Lowest verified|Free monthly allowances|Your usage|List-price sources|All verified options)", line):
            in_block = True
        elif not line.strip():
            in_block = False
        if in_block or re.match(r"\s*[-*]\s", line) and re.search(r"(?:INR|USD|₹|\$)\s?\d", line):
            nums |= grounding.numbers_in(line)
    return nums


def check_numbers(draft: str, allowed: set[float]) -> list[dict]:
    body = _URL.sub(" ", _MARK.sub(" ", draft))
    out = []
    for s in _sentences(body):
        bad = [n for n in grounding.numbers_in(s) if n >= grounding.MIN_NUMBER and not any(abs(n - a) < 0.011 for a in allowed)
               and not (1900 <= n <= 2100)]
        if bad:
            out.append(_issue("numbers", "high", f"number(s) {sorted(bad)} in the draft have no source", "remove", s.strip()))
    return out


def check_commitments(draft: str) -> list[dict]:
    return [_issue("commitments", "high", "the draft promises or implies a commitment (SLA, uptime, refund, discount, fix date or timeline)",
                   "remove", s.strip()) for s in _sentences(draft) if _COMMIT.search(s) and not _NEGATED.search(s)]


def check_safety(draft: str) -> list[dict]:
    _, found = redact_with_report(_URL.sub(" ", draft), allow_emails=PUBLIC_EMAILS)      # URLs (source links) are not secrets
    return [_issue("safety", "high", f"the draft contains sensitive data ({', '.join(sorted(set(found)))})", "ask_human")] if found else []


def check_validations(draft: str, validations: list[dict]) -> list[dict]:
    low = draft.lower()
    return [_issue("validations", "medium", f"a failed check is not mentioned in the draft: {v['detail'][:120]}", "add")
            for v in validations or [] if v.get("ok") is False and v["detail"].lower()[:40] not in low]


def check_capabilities(draft: str, matches: list[dict]) -> list[dict]:
    out = []
    for m in matches or []:
        for r in m["providers"]:
            if r["documented"] and not (r.get("url") and r.get("chunk_id")):
                out.append(_issue("capability", "high", f"{r['provider']} / {m['label']}: marked documented without a verified source", "remove"))
        for gap in m["gaps"]:
            for s in _sentences(draft):
                if gap.lower() in s.lower() and _DENIAL.search(s) and "not documented" not in s.lower() and "[gap]" not in s.lower():
                    out.append(_issue("capability", "medium", f"{gap} is a documentation gap but is described as unsupported", "remove", s.strip()))
    return out


def check_handoff(pkg: dict, plan: dict, *, pending_types: list[str] | None = None) -> list[dict]:
    """Problem 7: a handoff must state the problem, requirements, findings, owner, next action and the unresolved questions."""
    out = []
    pending = set(pending_types or [])
    f = pkg["findings"]
    if pkg["messages"] < 1:
        out.append(_issue("handoff", "medium", "no problem statement (the case has no messages)", "ask_human"))
    if not (f["validations"] or f["knowledge_answer"] or f["capabilities"]):
        out.append(_issue("handoff", "medium", "no findings recorded yet", "ask_human"))
    if not pkg["owner"] and "owner_action" not in pending:
        out.append(_issue("handoff", "medium", "no owner assigned or proposed", "ask_human"))
    if not pkg["next_action"] and "owner_action" not in pending:
        out.append(_issue("handoff", "medium", "no next action set or proposed", "ask_human"))
    unresolved = set(pkg["open_questions"]) | set(pkg["needs_human_followup"])
    for key in plan.get("missing_required", []):
        if key not in unresolved and key not in pkg["requirements"]:
            out.append(_issue("handoff", "medium", f"required item '{key}' is neither known nor listed as unresolved", "ask_human"))
    if pkg["workflow"] == "sales":
        for key in ("use_case", "api_needs", "volume"):
            if key not in pkg["requirements"] and key not in unresolved:
                out.append(_issue("handoff", "medium", f"sales requirement '{key}' is missing and not listed as unresolved", "ask_human"))
    return out


# ------------------------------------------------------------------------------------------------ review and revision
def _allowed_numbers(exec_result: dict, plan: dict, chunks: dict[str, str]) -> set[float]:
    k = exec_result.get("knowledge") or {}
    allowed = grounding.calc_numbers(k.get("tool_calls") or [])
    allowed |= _price_block_numbers(k.get("text", ""))
    for v in exec_result.get("validations", []):
        allowed |= grounding.numbers_in(v.get("detail", "") + " " + str(v.get("value", "")))
    for m in exec_result.get("capabilities", []):
        for r in m["providers"]:
            allowed |= grounding.numbers_in((r.get("statement") or "") + " " + (r.get("quote") or ""))
    allowed |= grounding.numbers_in(" ".join(str(x) for x in (plan.get("known_facts") or {}).values()))
    allowed |= grounding.numbers_in(" ".join(q["question"] for q in plan.get("questions", [])))
    return allowed


def review(exec_result: dict, plan: dict, *, handoff: dict | None = None, pending_types: list[str] | None = None,
           chunks: dict[str, str] | None = None) -> dict:
    """Pure review of an Executor result -> {verdict, issues, checks_run}. `chunks` maps chunk_id -> text (defaults to the corpus)."""
    chunks = chunks if chunks is not None else _corpus()
    draft, knowledge = exec_result["draft"], exec_result.get("knowledge") or {}
    allowed = _allowed_numbers(exec_result, plan, chunks)
    # cited passages also make their own numbers legitimate in the draft
    for c in knowledge.get("citations", []):
        allowed |= grounding.numbers_in(chunks.get(c.get("chunk_id"), ""))
    issues = (check_grounding(knowledge, chunks, allowed) + check_numbers(draft, allowed) + check_commitments(draft)
              + check_safety(draft) + check_validations(draft, exec_result.get("validations", []))
              + check_capabilities(draft, exec_result.get("capabilities", [])))
    # Two separate questions: is THIS DRAFT safe and supported (verdict), and is the CASE ready to hand off (handoff_ready)?
    handoff_issues = check_handoff(handoff, plan, pending_types=pending_types) if handoff is not None else []
    sev = {i["severity"] for i in issues}
    unfixable = any(i["fix"] == "ask_human" for i in issues)
    verdict = "pass" if not sev or sev == {"low"} else ("needs_human" if unfixable else "revise")
    return {"verdict": verdict, "issues": issues, "handoff_issues": handoff_issues,
            "handoff_ready": handoff is not None and not handoff_issues,
            "checks_run": ["grounding", "numbers", "commitments", "safety", "validations", "capability"] + (["handoff"] if handoff is not None else [])}


def _drop_sentences(text: str, bad: set[str]) -> str:
    out = []
    for line in text.split("\n"):
        kept = [s for s in re.split(r"(?<=[.!?])\s+", line) if s.strip() not in bad]
        if kept or not line.strip():
            out.append(" ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def revise(exec_result: dict, plan: dict, report: dict) -> dict:
    """Deterministic repair: drop the flagged sentences from the model-written knowledge text and re-draft. Never invents content."""
    bad = {i["sentence"] for i in report["issues"] if i["fix"] == "remove" and i.get("sentence")}
    k = dict(exec_result.get("knowledge") or {})
    if k.get("text"):
        k["text"] = _drop_sentences(k["text"], bad)
    kind = exec_result["kind"]
    draft = ex.draft_communication(kind, plan=plan, knowledge=k, validations=exec_result.get("validations"),
                                   capability_matches=exec_result.get("capabilities"), facts=plan.get("known_facts"))
    draft = _drop_sentences(draft, bad)                  # also catches flagged sentences that came from other sections
    return {**exec_result, "knowledge": k, "draft": draft}


def critique_and_revise(exec_result: dict, plan: dict, *, handoff: dict | None = None, pending_types: list[str] | None = None,
                        chunks: dict[str, str] | None = None, max_revisions: int = MAX_REVISIONS) -> dict:
    """Review; if fixable, revise and re-review (at most max_revisions); otherwise needs_human. -> {verdict, issues, revisions, result, history}"""
    current, history = exec_result, []
    report = review(current, plan, handoff=handoff, pending_types=pending_types, chunks=chunks)
    history.append({"round": 0, "verdict": report["verdict"], "issues": len(report["issues"])})
    revisions = 0
    while report["verdict"] == "revise" and revisions < max_revisions:
        revisions += 1
        current = revise(current, plan, report)
        report = review(current, plan, handoff=handoff, pending_types=pending_types, chunks=chunks)
        history.append({"round": revisions, "verdict": report["verdict"], "issues": len(report["issues"])})
    verdict = report["verdict"] if report["verdict"] != "revise" else "needs_human"       # still failing after the limit
    return {"verdict": verdict, "issues": report["issues"], "handoff_issues": report["handoff_issues"], "handoff_ready": report["handoff_ready"],
            "revisions": revisions, "result": current, "history": history}


def critique_case(con, case_id: str, exec_result: dict, plan: dict, *, chunks: dict[str, str] | None = None) -> dict:
    """Run the Critic on a case: handoff check, revision loop, trace steps, case state, and the pending draft approval updated with the
    revised draft and the verdict so the approver sees both. A needs_human verdict sets the case status; nothing is sent or applied."""
    import time
    t0 = time.time()
    pending = cases.list_pending(con, case_id)
    handoff = ex.build_handoff(con, case_id)
    out = critique_and_revise(exec_result, plan, handoff=handoff, pending_types=[a["type"] for a in pending], chunks=chunks)
    trace_id = cases.load_state(con, case_id).get("last_trace_id", "critic")
    cases.trace_step(con, trace_id, "critique", "critic", {"draft_chars": len(exec_result["draft"])},
                     {"verdict": out["verdict"], "revisions": out["revisions"], "issues": [i["check"] for i in out["issues"]]}, t0, case_id=case_id)
    state = cases.load_state(con, case_id)
    state["critic"] = {"verdict": out["verdict"], "revisions": out["revisions"], "issues": out["issues"], "history": out["history"],
                       "handoff_ready": out["handoff_ready"], "handoff_issues": out["handoff_issues"]}
    cases.save_state(con, case_id, state)
    for a in pending:
        if a["type"] == "external_send":
            payload = dict(a["payload"])
            payload.update({"draft": out["result"]["draft"], "critic": {
                "verdict": out["verdict"], "revisions": out["revisions"], "handoff_ready": out["handoff_ready"],
                "issues": [{k: i[k] for k in ("check", "severity", "detail")} for i in out["issues"]],
                "handoff_issues": [{k: i[k] for k in ("check", "severity", "detail")} for i in out["handoff_issues"]]}})
            cases.update_pending_payload(con, a["id"], payload, actor="critic")
    if out["verdict"] == "needs_human":
        cases.update_case(con, case_id, actor="critic", status="needs_human")
    cases.log_event(con, case_id, "critic", f"critic_{out['verdict']}", {"revisions": out["revisions"], "issues": [i["check"] for i in out["issues"]]})
    return out
