"""Regression gates (checklist X1): one command that says whether a change is safe to keep.

Run before every commit that touches retrieval, the guard, the Critic, the Planner or approvals, and again at the freeze.

Gates (thresholds in GATES below):
  guard_false_positives   legitimate development questions that the scope/ethics guard would refuse (rules + similarity gate)   must be 0
  approval_gates          approval invariants from eval/agent_eval.py (nothing applies without one authorised approval ...)       all hold
  critic_false_positives  clean, supported corpus sentences the Critic flags                                                     <= 3%
  here_violations         answers that state a HERE price although it is unverified                                              must be 0
  judged_correct_drop     judged-correct rate vs the saved baseline                                                              drop <= 2 points

The first three run offline in about a minute (local embeddings only, no gateway). The last two read a finished answer_eval run
(`python eval/answer_eval.py --n 400 --system rag`, about 50 minutes on the gateway; newest report with >= 300 rows, or --report FILE).
Without such a report those two are INCOMPLETE, which is never reported as a pass.

Exit code: 0 = every gate passed, 1 = a gate failed, 2 = nothing failed but a gate could not be checked.
Development split only. The 100-question holdout is only touched with --include-holdout, at the freeze, and the result is recorded, never tuned on.
Usage: python eval/gates.py [--report FILE] [--set-baseline] [--include-holdout] [--skip-critic]
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

REPORTS = ROOT / "eval" / "reports"
BASELINE = ROOT / "eval" / "baselines" / "answer_eval_baseline.json"
MIN_ROWS = 300      # a "full" answer run
GATES = {"guard_false_positives": 0, "critic_false_positive_rate": 0.03, "here_violations": 0, "judged_correct_drop_points": 2.0}


# ------------------------------------------------------------------------------------------------ individual gates
def gate_guard(items) -> dict:
    """Would the guard refuse a legitimate question? Same order as answer(): rules, then (only without a strong domain term) the subject
    list and the similarity gate. The borderline model second opinion needs the gateway and is only counted, not run."""
    from lbs_rag.generate import guard
    from lbs_rag.retrieve.hybrid import dense_search
    refused, borderline = [], 0
    for it in items:
        q = it["Question"]
        g = guard.check(q)
        if g["refuse"]:
            refused.append((it["ID"], f"rule:{g['category']}", q[:90]))
            continue
        if guard.has_strong_domain_term(q):
            continue
        top = dense_search(q, n=1)
        sim = top[0]["score"] if top else 0.0
        off = guard.domain_gate(q, sim)
        if off:
            refused.append((it["ID"], f"gate:{off['category']}", q[:90]))
        elif sim < guard.BORDERLINE_SIM:
            borderline += 1
    n = len(refused)
    return {"name": "guard_false_positives", "value": n, "detail": f"{n} of {len(items)} refused; {borderline} borderline would get a model second opinion (not run)",
            "ok": n <= GATES["guard_false_positives"], "items": refused[:20]}


def gate_approvals() -> dict:
    import agent_eval
    good = agent_eval.eval_gates()
    ok_n, n = agent_eval.LAST_GATES
    return {"name": "approval_gates", "value": f"{ok_n}/{n}", "detail": f"{ok_n}/{n} invariants hold", "ok": bool(good) and n > 0}


def gate_critic() -> dict:
    import agent_eval
    agent_eval.eval_critic()
    c = agent_eval.LAST_CRITIC
    rate = c["clean_flagged"] / max(c["clean_n"], 1)
    return {"name": "critic_false_positives", "value": round(rate, 4),
            "detail": f"{c['clean_flagged']}/{c['clean_n']} clean sentences flagged ({rate:.1%}); limit {GATES['critic_false_positive_rate']:.0%}",
            "ok": rate <= GATES["critic_false_positive_rate"], "caught": c["caught"]}


def load_report(path: Path | None):
    """Newest answer_eval run with >= MIN_ROWS rag rows (or the given file). Returns (path, rows) or (None, [])."""
    cands = [path] if path else sorted(REPORTS.glob("answer_eval_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cands:
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        rows = [r for r in rows if r.get("system") == "rag" and "error" not in r]
        if len(rows) >= MIN_ROWS or path:
            return p, rows
    return None, []


def summarise_rows(rows: list[dict]) -> dict:
    j = [r["judge"] for r in rows]
    return {"n": len(rows), "correct": j.count("correct"), "partial": j.count("partial"), "incorrect": j.count("incorrect"),
            "here_violations": sum(bool(r["here_violation"]) for r in rows)}


def gate_here(s: dict | None, src: str) -> dict:
    if s is None:
        return {"name": "here_violations", "value": None, "detail": "no full answer run found (python eval/answer_eval.py --n 400 --system rag)", "ok": None}
    return {"name": "here_violations", "value": s["here_violations"], "detail": f"{s['here_violations']} in {s['n']} answers ({src})",
            "ok": s["here_violations"] <= GATES["here_violations"]}


def gate_drop(s: dict | None, src: str) -> dict:
    if s is None or not BASELINE.exists():
        return {"name": "judged_correct_drop", "value": None, "ok": None,
                "detail": "no full answer run found" if s is None else "no baseline saved (run with --set-baseline on a run you accept)"}
    b = json.loads(BASELINE.read_text(encoding="utf-8"))
    now, then = s["correct"] / s["n"], b["correct"] / b["n"]
    drop = (then - now) * 100
    return {"name": "judged_correct_drop", "value": round(drop, 2), "ok": drop <= GATES["judged_correct_drop_points"],
            "detail": f"correct {s['correct']}/{s['n']} = {now:.1%} now vs {b['correct']}/{b['n']} = {then:.1%} baseline ({b.get('label', '')}); "
                      f"change {-drop:+.1f} points; limit -{GATES['judged_correct_drop_points']:.0f}"}


# ------------------------------------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, help="answer_eval_*.jsonl to use (default: newest run with >= 300 rag rows)")
    ap.add_argument("--set-baseline", action="store_true", help="save the chosen report as the baseline future runs are compared with")
    ap.add_argument("--include-holdout", action="store_true", help="also check the guard on the 100 holdout questions (freeze only; never tune on it)")
    ap.add_argument("--skip-critic", action="store_true")
    a = ap.parse_args()

    items = load_items("eval") + (load_items("holdout") if a.include_holdout else [])
    path, rows = load_report(a.report)
    s = summarise_rows(rows) if rows else None
    if a.set_baseline:
        if s is None:
            sys.exit("no full answer run to save as baseline")
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({**s, "label": f"{path.name}, saved {time.strftime('%Y-%m-%d')}"}, indent=2), encoding="utf-8")
        print(f"baseline saved: {s} from {path.name}")
        return

    results = [gate_guard(items), gate_approvals()]
    if not a.skip_critic:
        results.append(gate_critic())
    src = path.name if path else ""
    results += [gate_here(s, src), gate_drop(s, src)]

    print("\n" + "=" * 78 + f"\nREGRESSION GATES ({len(items)} questions in the guard check)\n" + "=" * 78)
    for r in results:
        mark = {True: "PASS      ", False: "FAIL      ", None: "INCOMPLETE"}[r["ok"]]
        print(f"{mark} {r['name']:<24} {r['detail']}")
        for it in r.get("items", [])[:10]:
            print("           ", it)
    failed = [r["name"] for r in results if r["ok"] is False]
    incomplete = [r["name"] for r in results if r["ok"] is None]
    verdict = "FAIL" if failed else "INCOMPLETE" if incomplete else "PASS"
    print(f"\nVERDICT: {verdict}" + (f"  failed: {failed}" if failed else "") + (f"  not checked: {incomplete}" if incomplete else ""))
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"gates_{time.strftime('%Y%m%d_%H%M%S')}.json").write_text(
        json.dumps({"verdict": verdict, "report_used": src, "results": results}, indent=2, default=str), encoding="utf-8")
    sys.exit(1 if failed else 2 if incomplete else 0)


if __name__ == "__main__":
    main()
