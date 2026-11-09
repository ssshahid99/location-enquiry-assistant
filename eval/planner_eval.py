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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="eval")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
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
