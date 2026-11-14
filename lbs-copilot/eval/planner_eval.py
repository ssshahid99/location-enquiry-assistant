"""Planner evaluation on the ground-truth questions (development split by default; the holdout stays untouched).

Answer key: the ground-truth `intent` column, mapped to an expected workflow for the CLEAR intents only. Knowledge-style intents
(parameter_question, response_fields, policy_question, ...) can reasonably be either, so they are excluded from accuracy and only
reported. Also reports escalation recommendation vs `should_escalate`, and how many questions the Planner would ask something about.
Usage: python eval/planner_eval.py [--split eval|all] [--show]
"""
import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

from lbs_rag.agent.planner import plan  # noqa: E402

SALES = {"pricing_quote", "pricing_comparison", "pricing_estimate", "free_tier_question", "capability_matching", "capability_fit",
         "discovery_qualification", "contract_request", "competitive_comparison", "solution_design", "licensing_fit"}
SUPPORT = {"error_resolution", "parameter_troubleshooting", "production_incident", "billing_incident", "billing_dispute",
           "security_incident", "coverage_error", "data_quality", "accuracy_regression", "performance_question"}


def coverage(path: str, with_model: bool, show: bool) -> None:
    """A5: how often do the rules miss real messages? Run on the TESTER messages, never tune on the development split.
    Reports rules-only accuracy, the 'unclear' rate, and (with --fallback) what the model fallback fixes and what it breaks."""
    import csv
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    n = len(rows)
    rules_ok = unclear = fb_fixed = fb_broke = fb_tried = 0
    misses = []
    for r in rows:
        msg, exp = r["message"], (r.get("expected_workflow") or "").strip().lower()
        rp = plan(msg, model_fallback=False)
        got = rp["workflow"]
        rules_ok += got == exp
        unclear += got == "unclear"
        final = got
        if with_model and got == "unclear":
            fb_tried += 1
            final = plan(msg, model_fallback=True)["workflow"]
            fb_fixed += final == exp and got != exp
            fb_broke += got == exp and final != exp
        if final != exp:
            misses.append((exp, got, final, msg[:100]))
    print(f"tester messages: {n}")
    print(f"rules only: {rules_ok}/{n} = {rules_ok / n:.1%} correct; 'unclear' {unclear}/{n} = {unclear / n:.1%}")
    if with_model:
        print(f"model fallback tried on {fb_tried}: fixed {fb_fixed}, broke {fb_broke}; with fallback {(rules_ok + fb_fixed - fb_broke) / n:.1%} correct")
        print("Keep the fallback ON only if it fixes clearly more than it breaks AND the unclear rate above justifies the extra model call.")
    if show:
        for m in misses[:30]:
            print("MISS (expected, rules, final):", m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="eval")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--messages", help="CSV of TESTER messages (columns: message, expected_workflow = sales|support|unclear) to measure rule coverage")
    ap.add_argument("--fallback", action="store_true", help="with --messages: also try the model fallback on messages the rules leave 'unclear' (uses the gateway)")
    a = ap.parse_args()
    if a.messages:
        return coverage(a.messages, a.fallback, a.show)
    items = load_items(None if a.split == "all" else a.split)
    ok = tot = 0
    conf: collections.Counter = collections.Counter()
    wrong, esc = [], collections.Counter()
    knowledge = collections.Counter()
    asks = 0
    for it in items:
        r = plan(it["Question"])
        exp = "sales" if it["intent"] in SALES else "support" if it["intent"] in SUPPORT else None
        if exp:
            tot += 1
            ok += r["workflow"] == exp
            conf[(exp, r["workflow"])] += 1
            if r["workflow"] != exp:
                wrong.append((it["ID"], it["intent"], exp, r["workflow"], r["category"], it["Question"][:110]))
        else:
            knowledge[r["workflow"]] += 1
        truth = str(it["should_escalate"]).lower() == "true"
        esc[(truth, r["escalation"]["recommend"])] += 1
        asks += bool(r["questions"])
    print(f"split={a.split} items={len(items)}")
    print(f"workflow accuracy on clear intents: {ok}/{tot} = {ok / tot:.1%}")
    print("confusion (expected -> predicted):", dict(conf))
    print("knowledge-style intents classified as:", dict(knowledge))
    tp, fp, fn = esc[(True, True)], esc[(False, True)], esc[(True, False)]
    print(f"escalation recommendation: TP={tp} FP={fp} FN={fn}  precision={tp / max(tp + fp, 1):.2f} recall={tp / max(tp + fn, 1):.2f}")
    print(f"questions the Planner would ask something about: {asks}/{len(items)}")
    if a.show:
        for w in wrong[:25]:
            print("MISS", w)


if __name__ == "__main__":
    main()
