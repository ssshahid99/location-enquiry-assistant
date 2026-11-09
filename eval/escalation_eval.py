"""Escalation precision/recall against should_escalate (17 of 500 are True, so accuracy alone is meaningless).
Rules are tuned on the eval split; the holdout split is reported separately and only looked at for the final run.
Usage: python eval/escalation_eval.py [--split eval|holdout|all] [--show]"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

from lbs_rag.safety.escalation import check  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="eval")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    items = load_items(None if a.split == "all" else a.split)
    tp = fp = fn = tn = 0
    for it in items:
        truth = str(it["should_escalate"]).lower() == "true"
        r = check(it["Question"])
        if truth and r["escalate"]:
            tp += 1
        elif truth:
            fn += 1
            if a.show:
                print(f"MISSED   [{it['intent']}] {it['ID']}: {it['Question'][:150]}")
        elif r["escalate"]:
            fp += 1
            if a.show:
                print(f"FALSE+   [{it['intent']}] {it['ID']} ({r['category']}, /{r['matched']}/): {it['Question'][:150]}")
        else:
            tn += 1
    p = tp / (tp + fp) if tp + fp else 0
    rc = tp / (tp + fn) if tp + fn else 0
    print(f"split={a.split} n={len(items)}  TP={tp} FP={fp} FN={fn} TN={tn}  precision={p:.2f} recall={rc:.2f}")


if __name__ == "__main__":
    main()
