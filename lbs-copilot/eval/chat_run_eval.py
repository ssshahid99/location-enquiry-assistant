"""Run the real chat (real model, real retrieval) over a ground-truth tier and measure Activities 12 and 14 and the Planner.

Per question: route, schema validity of the 7 outputs, every tool call (schema ok? registered?), tools used vs the gold tools, model calls, latency.
The core tier is a scoreboard: this script only MEASURES; nothing is tuned on it. Gateway: one request at a time, so do not use the Chat tab meanwhile.
Usage: python eval/chat_run_eval.py [--tier core] [--n 80]
"""
import argparse
import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from chat_eval import gold_route  # noqa: E402
from load_gt import load_items  # noqa: E402

from lbs_rag.chat.chat import chat  # noqa: E402
from lbs_rag.store.db import connect  # noqa: E402


def gold_tools(it: dict) -> set[str]:
    raw = str(it.get("expected_tools") or "")
    return {t.strip() for t in raw.split(",") if t.strip() and t.strip() != "nan"}


def pct(a: float, b: float) -> str:
    return f"{a / b:.1%}" if b else "n/a"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="core")
    ap.add_argument("--n", type=int, default=0)
    a = ap.parse_args()
    items = load_items(a.tier)
    if a.n:
        items = items[:a.n]
    con = connect(Path(tempfile.mkdtemp()) / "chat_run.db")
    rows = []
    for k, it in enumerate(items, 1):
        t = time.time()
        try:
            r = chat(con, it["Question"], f"EV-{it['ID']}", customer="eval", persist=False)
            err = None
        except Exception as e:  # noqa: BLE001
            r, err = None, f"{type(e).__name__}: {e}"
        sec = time.time() - t
        if r is None:
            rows.append({"id": it["ID"], "error": err, "sec": round(sec, 1)})
            continue
        calls = r["tool_calls"]
        used = {c["tool"] for c in calls if c["ok"]}
        rows.append({"id": it["ID"], "tier": it.get("tier"), "intent": it["intent"], "route": r["route"], "gold_route": gold_route(it),
                     "route_source": r["route_source"], "schema_errors": r["schema_errors"], "tool_calls": len(calls),
                     "tool_schema_ok": sum(1 for c in calls if c["schema_ok"]), "tool_ok": sum(1 for c in calls if c["ok"]),
                     "unregistered": sum(1 for c in calls if c["error"] == "unregistered tool"), "tools_used": sorted(used),
                     "gold_tools": sorted(gold_tools(it)), "model_calls": r["model_calls"], "sec": round(sec, 1),
                     "cited": bool(r["outputs"]["response_draft"]["citations"]), "refusal": r["outputs"]["response_draft"]["is_refusal"],
                     "approval_pending": bool(r.get("action_id") or r.get("record_action_id")),
                     "next_action": r["outputs"]["next_action"]["action_type"], "answer": r["text"][:600]})
        if k % 10 == 0:
            print(f"  {k}/{len(items)}", flush=True)
    out = ROOT / "eval" / "reports" / f"chat_run_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    ok = [r for r in rows if "error" not in r]
    print(f"\n== chat over {a.tier} (n={len(rows)}, crashed={len(rows) - len(ok)}) ==")
    print(f"outputs valid against the 7 schemas   {sum(1 for r in ok if not r['schema_errors'])}/{len(ok)} = {pct(sum(1 for r in ok if not r['schema_errors']), len(ok))}   (target >= 98%)")
    tc, ts = sum(r["tool_calls"] for r in ok), sum(r["tool_schema_ok"] for r in ok)
    print(f"tool calls passing schema validation  {ts}/{tc} = {pct(ts, tc)}   (target >= 98%)")
    print(f"calls to unregistered tools           {sum(r['unregistered'] for r in ok)}   (target 0)")
    print(f"tool calls that failed to run         {tc - sum(r['tool_ok'] for r in ok)}")
    rt = Counter((r["gold_route"], r["route"]) for r in ok)
    print("route (gold -> chosen): " + ", ".join(f"{g}->{c} {n}" for (g, c), n in sorted(rt.items())))
    print(f"route accuracy                        {pct(sum(n for (g, c), n in rt.items() if g == c), len(ok))}")
    exact = sum(1 for r in ok if set(r["tools_used"]) == set(r["gold_tools"]))
    cover = sum(1 for r in ok if set(r["gold_tools"]) <= set(r["tools_used"]))
    print(f"tools used == gold tools              {exact}/{len(ok)} = {pct(exact, len(ok))};  all gold tools used {cover}/{len(ok)} = {pct(cover, len(ok))}")
    miss = Counter(t for r in ok for t in set(r["gold_tools"]) - set(r["tools_used"]))
    extra = Counter(t for r in ok for t in set(r["tools_used"]) - set(r["gold_tools"]))
    print(f"gold tools not used: {dict(miss)}   tools used that gold lacks: {dict(extra)}")
    rag = [r for r in ok if r["route"] == "rag" and not r["refusal"]]
    print(f"RAG answers with at least one citation {sum(r['cited'] for r in rag)}/{len(rag)} = {pct(sum(r['cited'] for r in rag), len(rag))}")
    secs = sorted(r["sec"] for r in ok)
    if secs:
        print(f"latency p50 {statistics.median(secs):.1f}s  p95 {secs[int(0.95 * (len(secs) - 1))]:.1f}s   model calls per turn: "
              f"mean {statistics.mean(r['model_calls'] for r in ok):.1f}, max {max(r['model_calls'] for r in ok)}")
    print(f"refusals {sum(r['refusal'] for r in ok)}; escalations proposed {sum(r['route'] == 'escalation' for r in ok)}; "
          f"record updates proposed {sum(r['next_action'] == 'propose_record_update' for r in ok)}")
    bad = [r for r in ok if r["schema_errors"]]
    for r in bad[:5]:
        print("  schema errors:", r["id"], r["schema_errors"][:2])
    print(f"\nper-item results: {out}")


if __name__ == "__main__":
    main()
