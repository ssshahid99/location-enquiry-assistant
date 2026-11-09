"""Agent evaluation (project minutes: "test routing, validators, approval gates and handoffs"; plus Critic quality and cost/latency).

Parts (1-3 need no model and no gateway; 4 uses the real RAG and the gateway, so it is opt-in):
  1. routing        route every development-split question and score the CLEAR classes against the ground-truth intents
  2. approval gates programmatic invariants: nothing applies without approval, only a listed human approves, rejection needs a reason,
                    decisions are final, an approved action applies once, external sends are never sent
  3. critic         seeded wrong claims injected into real corpus sentences: detection rate per seed type, false-positive rate on clean ones
  4. chain          (--chain N) real messages through Router -> Planner -> Executor -> Critic: route mix, Critic verdicts, handoff-ready rate,
                    approvals per case, latency per route, model calls per route
Development split only (the holdout stays untouched).  Usage: python eval/agent_eval.py [--chain 24]
"""
import argparse
import collections
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

from lbs_rag.agent import critic, executor, orchestrator, router  # noqa: E402
from lbs_rag.store import cases, handlers  # noqa: E402
from lbs_rag.store.db import connect  # noqa: E402

BUCKETS = {   # ground-truth intent -> expected route(s)
    "contract_request": {"human"}, "security_incident": {"human"},
    "discovery_qualification": {"agent"},
    "production_incident": {"agent", "human"}, "billing_incident": {"agent"}, "data_quality": {"agent"}, "accuracy_regression": {"agent"},
    "pricing_quote": {"rag"}, "pricing_comparison": {"rag"}, "pricing_estimate": {"rag"}, "free_tier_question": {"rag"},
    "parameter_question": {"rag"}, "response_fields": {"rag"}, "policy_question": {"rag"},
}


# ------------------------------------------------------------------------------------------------ 1. routing
def eval_routing(items):
    mix, per_bucket, esc_ok, esc_n, wrong = collections.Counter(), collections.defaultdict(lambda: [0, 0]), 0, 0, []
    for it in items:
        d = router.route(it["Question"])
        mix[d["route"]] += 1
        exp = BUCKETS.get(it["intent"])
        if exp:
            per_bucket[it["intent"]][1] += 1
            ok = d["route"] in exp
            per_bucket[it["intent"]][0] += ok
            if not ok:
                wrong.append((it["ID"], it["intent"], d["route"], d["reason"], it["Question"][:90]))
        if str(it["should_escalate"]).lower() == "true":
            esc_n += 1
            esc_ok += d["route"] in ("agent", "human")
    print("== 1. ROUTING (development split) ==")
    print("route mix:", {k: f"{v} ({v / len(items):.0%})" for k, v in mix.items()})
    tot_ok = sum(v[0] for v in per_bucket.values())
    tot = sum(v[1] for v in per_bucket.values())
    print(f"clear classes: {tot_ok}/{tot} = {tot_ok / tot:.1%} correct")
    for k, (ok, n) in sorted(per_bucket.items()):
        print(f"  {k:<26} {ok}/{n} -> expected {sorted(BUCKETS[k])}")
    print(f"questions that should escalate reach the agent or a person: {esc_ok}/{esc_n}")
    return wrong


# ------------------------------------------------------------------------------------------------ 2. approval gates
def eval_gates():
    """Runs the gate checks against a STUBBED RAG, then restores the real one (a later part of this script uses the real RAG)."""
    import lbs_rag.generate.answer as A
    saved = (A.answer, orchestrator.answer)
    try:
        return _eval_gates_inner(A)
    finally:
        A.answer, orchestrator.answer = saved


def _eval_gates_inner(A):
    A.answer = lambda q, **k: {"text": "Ola rate limiting returns a 429 response [1].", "citations": [], "abstained": False, "refused": False,
                               "escalate": False, "reason": None, "trace_id": "t", "tool_calls": []}
    orchestrator.answer = A.answer
    con = connect(Path(tempfile.mkdtemp()) / "gates.db")
    checks = []

    def check(name, fn):
        try:
            checks.append((name, bool(fn())))
        except Exception as e:  # noqa: BLE001
            checks.append((name, False))
            print("   gate error:", name, type(e).__name__, e)

    r = orchestrator.handle_message(con, "PRODUCTION: Ola Distance Matrix has returned 5xx for all requests across Mumbai since 14:05 IST.")
    cid = r["case_id"]
    ids = {a["type"]: a["id"] for a in r["approvals"]}
    check("full agent run applies nothing (owner unset, tracker empty)", lambda: cases.get_case(con, cid)["owner"] is None and cases.get_tracker(con, cid) == {})
    check("full agent run leaves every request pending", lambda: len(r["approvals"]) >= 3 and all(a["status"] == "pending" for a in cases.list_pending(con, cid)))
    for who in ("agent", "planner", "", "someone else"):
        def refused(who=who):
            try:
                handlers.approve_and_apply(con, ids["external_send"], approver=who)
                return False
            except ValueError:
                return True
        check(f"approver '{who}' is refused", refused)
    check("rejection without a reason is refused", lambda: _raises(lambda: handlers.reject(con, ids["external_send"], approver="deepshikha", note="")))
    check("still nothing applied after the refused attempts", lambda: cases.get_case(con, cid)["owner"] is None)
    done = handlers.approve_and_apply(con, ids["external_send"], approver="deepshikha")
    check("approved external_send is recorded, never sent", lambda: "nothing was sent" in done["applied_result"].lower())
    check("a decision is final (cannot re-approve)", lambda: _raises(lambda: handlers.approve_and_apply(con, ids["external_send"], approver="deepshikha")))
    check("an approved action cannot be applied twice", lambda: _raises(lambda: cases.apply_approved(con, ids["external_send"], lambda p: None)))
    check("a decided request cannot be edited", lambda: _raises(lambda: cases.update_pending_payload(con, ids["external_send"], {"x": 1})))
    handlers.reject(con, ids["tracker_update"], approver="deepshikha", note="wrong customer")
    check("rejected tracker update never reaches the tracker", lambda: cases.get_tracker(con, cid) == {})
    check("rejection reason is in the case history", lambda: any("wrong customer" in m["content"] for m in cases.get_case_history(con, cid)))
    check("every state change is in the case timeline", lambda: {"approval_requested", "approval_approved", "action_applied", "approval_rejected"} <= {e["event"] for e in cases.case_timeline(con, cid)})
    h = orchestrator.prepare_handoff(con, cid)
    check("handoff changes owner only after approval", lambda: cases.get_case(con, cid)["status"] != "handed_off")
    handlers.approve_and_apply(con, h["approval_id"], approver="deepshikha")
    check("approved handoff hands the case off", lambda: cases.get_case(con, cid)["status"] == "handed_off")
    ok = sum(c[1] for c in checks)
    print(f"\n== 2. APPROVAL GATES ==\n{ok}/{len(checks)} invariants hold")
    for name, good in checks:
        if not good:
            print("   FAIL:", name)
    return ok == len(checks)


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


# ------------------------------------------------------------------------------------------------ 3. critic seeded wrong claims
def _supported_sentences(limit=80):
    import re
    from lbs_rag.index.bm25 import load_chunks
    out = []
    for c in load_chunks():
        if c["kind"] == "stackoverflow":
            continue
        body = c["text"].split("\n\n", 1)[-1]
        for s in re.split(r"(?<=[.!?])\s+", body):
            s = s.strip()
            if 8 <= len(s.split()) <= 35 and re.search(r"(?<![\w.])\d{2,}(?![\w.])", s) and "http" not in s and "|" not in s and "```" not in s:
                out.append((c, s))
                break
        if len(out) >= limit:
            break
    return out


def eval_critic():
    import re
    pairs = _supported_sentences()
    chunks = {c["chunk_id"]: c["text"] for c, _ in pairs}
    plan = {"questions": [], "needs_human_followup": [], "known_facts": {}, "missing_required": []}
    seeds = {
        "invented number": (lambda c, s: re.sub(r"(?<![\w.])(\d{2,})(?![\w.])", lambda m: str(int(m.group(1)) + 7), s, count=1), {"grounding", "numbers"}),
        "wrong provider": (lambda c, s: None if c["provider"] == "Google Maps Platform" else f"Google Maps Platform says: {s}", {"grounding"}),
        "unrelated claim": (lambda c, s: "Satellites transmit atmospheric corrections regularly throughout constellations worldwide.", {"grounding"}),
        "commitment": (lambda c, s: f"{s} We guarantee a fix within 4 hours.", {"commitments"}),
        "personal data": (lambda c, s: f"{s} Contact john.doe@example.com.", {"safety"}),
    }

    def result(c, text):
        k = {"text": f"{text} [1]", "citations": [{"n": 1, "provider": c["provider"], "doc_url": c["doc_url"], "chunk_id": c["chunk_id"]}], "tool_calls": []}
        return {"kind": "support_reply", "draft": executor.draft_communication("support_reply", plan=plan, knowledge=k),
                "knowledge": k, "validations": [], "capabilities": []}

    clean_flagged = sum(1 for c, s in pairs if critic.review(result(c, s), plan, chunks=chunks)["issues"])
    print("\n== 3. CRITIC on seeded wrong claims (real corpus sentences) ==")
    print(f"clean supported sentences wrongly flagged: {clean_flagged}/{len(pairs)} = {clean_flagged / len(pairs):.1%}")
    for name, (make, expected) in seeds.items():
        n = hit = 0
        for c, s in pairs:
            seeded = make(c, s)
            if seeded is None:
                continue
            n += 1
            rep = critic.review(result(c, seeded), plan, chunks=chunks)
            hit += any(i["check"] in expected for i in rep["issues"])
        print(f"  {name:<18} caught {hit}/{n} = {hit / max(n, 1):.1%}")
    from lbs_rag.tools.capabilities import match_capabilities
    caps = match_capabilities("optimise the order of stops", ["Ola Maps", "HERE"])
    c0 = pairs[0][0]
    ex = result(c0, pairs[0][1])
    ex["capabilities"] = caps
    ex["draft"] += "\nHERE does not support route optimisation."
    print("  gap written up as 'unsupported':", "caught" if any(i["check"] == "capability" for i in critic.review(ex, plan, chunks=chunks)["issues"]) else "MISSED")


# ------------------------------------------------------------------------------------------------ 4. chain on real messages
def eval_chain(items, n):
    sample = [i for i in items if not str(i["intent"]).startswith("pricing")][::max(1, len(items) // n)][:n]
    con = connect(Path(tempfile.mkdtemp()) / "chain.db")
    by_route = collections.defaultdict(list)
    verdicts, ready, approvals, errors = collections.Counter(), [0, 0], [], []
    for it in sample:
        try:
            r = orchestrator.handle_message(con, it["Question"])
        except Exception as e:  # noqa: BLE001
            errors.append((it["ID"], f"{type(e).__name__}: {e}"))
            continue
        by_route[r["route"]].append(r["elapsed_ms"])
        if r.get("critic"):
            verdicts[r["critic"]["verdict"]] += 1
            ready[1] += 1
            ready[0] += bool(r["critic"]["handoff_ready"])
        if r.get("approvals") is not None:
            approvals.append(len(r["approvals"]))
    print(f"\n== 4. CHAIN on {len(sample)} real questions (real RAG) ==")
    for route, ms in by_route.items():
        print(f"  {route:<8} n={len(ms):<3} mean latency {sum(ms) / len(ms) / 1000:.1f}s   model calls per message: "
              f"{'0 (code only)' if route == 'human' else '1-3 (RAG: rewrite, price extraction, generation)'}")
    print("  critic verdicts:", dict(verdicts), "| handoff ready:", f"{ready[0]}/{ready[1]}")
    if approvals:
        print(f"  pending approvals per case: mean {sum(approvals) / len(approvals):.1f}, max {max(approvals)}")
    for e in errors:
        print("  ERROR", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", type=int, default=0, help="also run N real questions through the full chain (uses the gateway)")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    items = load_items("eval")
    wrong = eval_routing(items)
    if a.show:
        for w in wrong[:25]:
            print("  MISS", w)
    gates_ok = eval_gates()
    eval_critic()
    if a.chain:
        eval_chain(items, a.chain)
    sys.exit(0 if gates_ok else 1)


if __name__ == "__main__":
    main()
