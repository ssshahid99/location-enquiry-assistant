"""Offline evaluation of the chat Planner and the ownership / escalation router (Activities 15-16). No model call: rules only (use_model=False).

Gold: route = escalation if should_escalate; rag if the gold tools include doc_search or calculator; llm otherwise.
      owner / severity / should_escalate come straight from the ground-truth labels.
Targets from the Activities sheet (16): owner accuracy >= 90%, escalation recall >= 0.90, escalation precision >= 0.80.
Usage: python eval/chat_eval.py [--tier eval|working|core]     (core is a scoreboard: look, never tune)
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

from lbs_rag.agent import planner as case_rules  # noqa: E402
from lbs_rag.chat import drafter, planner  # noqa: E402
from lbs_rag.safety import escalation  # noqa: E402


def gold_route(it: dict) -> str:
    tools = str(it.get("expected_tools") or "")
    if str(it.get("should_escalate")).lower() == "true":
        return "escalation"
    return "rag" if ("doc_search" in tools or "calculator" in tools or not tools or tools == "nan") else "llm"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="eval")
    ap.add_argument("--model", action="store_true", help="also use the model fallback for ambiguous cases (uses the gateway)")
    a = ap.parse_args()
    items = load_items(a.tier)
    conf: Counter = Counter()
    own_ok = own_n = 0
    tp = fp = fn = 0
    sev_ok = sev_n = 0
    wrong_owner: Counter = Counter()
    for it in items:
        q = it["Question"]
        p = planner.plan(q, {}, use_model=a.model)
        route = "rag" if p["route"] == "refuse" else p["route"]
        g = gold_route(it)
        conf[(g, route, p["source"])] += 1
        esc_pred, esc_gold = route == "escalation", g == "escalation"
        tp += esc_pred and esc_gold
        fp += esc_pred and not esc_gold
        fn += (not esc_pred) and esc_gold
        cls = case_rules.classify(q)
        cat = (escalation.check(q)["category"] or p["signals"].get("category")) if esc_pred else None
        rl = drafter.role(q, route, cat or ("human_request" if esc_pred else None), cls["workflow"], cls["category"])
        gold_owner = str(it.get("owner") or "").strip()
        if gold_owner and gold_owner.lower() != "nan":
            own_n += 1
            own_ok += rl["owner"] == gold_owner
            if rl["owner"] != gold_owner:
                wrong_owner[(gold_owner, rl["owner"])] += 1
        gs = str(it.get("severity") or "").strip()
        if gs.startswith("SEV"):
            sev_n += 1
            sev_ok += rl["severity"] == gs
    n = len(items)
    print(f"items: {n} (tier={a.tier})")
    print("route (gold -> predicted): " + ", ".join(f"{g}->{r}[{s}] {c}" for (g, r, s), c in sorted(conf.items())))
    rt_ok = sum(c for (g, r, _), c in conf.items() if g == r)
    print(f"route accuracy       {rt_ok}/{n} = {rt_ok / n:.1%}")
    print(f"escalation recall    {tp}/{tp + fn} = {tp / max(1, tp + fn):.2f}   (target >= 0.90)")
    print(f"escalation precision {tp}/{tp + fp} = {tp / max(1, tp + fp):.2f}   (target >= 0.80)")
    print(f"owner accuracy       {own_ok}/{own_n} = {own_ok / max(1, own_n):.1%}   (target >= 90%)")
    print(f"severity accuracy    {sev_ok}/{sev_n} = {sev_ok / max(1, sev_n):.1%}")
    print("most common owner mismatches (gold -> predicted): " + "; ".join(f"{g} -> {p} x{c}" for (g, p), c in wrong_owner.most_common(5)))


if __name__ == "__main__":
    main()
