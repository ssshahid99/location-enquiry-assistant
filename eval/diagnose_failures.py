"""Why did an answer fail? Splits every non-correct answer (core excluded: it is the scoreboard) into causes:

  gold_unreachable   no verbatim gold quote is on the fetched page (paraphrased gold): retrieval cannot be blamed or fixed
  page_missed        the gold page is not in the top-20 at all
  chunk_missed       the gold page is there but the gold quote is not in the top-20 chunks
  rank_6_to_20       the quote is in the top-20 but not the top-6 (a larger k / better ordering would fix it)
  generation         the quote WAS in the top-6 but the answer is still judged wrong (prompt / grounding / judge)
  no_quote_gold      the gold has no verbatim quote at all (summary only)

Usage: python eval/diagnose_failures.py [--report FILE]   -> eval/reports/diagnosis_*.csv (embedding + rewrite calls only, no answers)
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

import pandas as pd  # noqa: E402
from chunk_recall import norm, quotes  # noqa: E402

from lbs_rag.index.bm25 import load_chunks  # noqa: E402
from lbs_rag.retrieve.hybrid import retrieve  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(sorted((ROOT / "eval" / "reports").glob("answer_eval_20261114_213203.jsonl"))[-1]))
    a = ap.parse_args()
    rows = [json.loads(x) for x in open(a.report, encoding="utf-8")]
    rows = [r for r in rows if r["judge"] != "correct" and r["tier"] != "core"]
    cit = pd.read_csv(ROOT / "data" / "ground_truth" / "gt_v3_citations.csv")
    byid = {i: g for i, g in cit.groupby("ID")}
    doc_text: dict[str, str] = defaultdict(str)
    for c in load_chunks():
        doc_text[c["doc_url"]] += " " + norm(c["text"])
    out = []
    for n, r in enumerate(rows, 1):
        g = byid.get(r["id"])
        golds, urls = [], set()
        if g is not None:
            urls = set(g["doc_url"])
            for _, c in g[g["quote_or_summary"] == "verbatim"].iterrows():
                for q in quotes(c["span"]):
                    if q in doc_text.get(c["doc_url"], ""):
                        golds.append(q)
        if not urls:
            cause, rank = "no_quote_gold", None
        elif not golds:
            cause, rank = "gold_unreachable", None
        else:
            hits = retrieve(r["question"], k=20, rewrite=True, route=True)
            texts = [norm(h["text"]) for h in hits]
            ranks = [next((i + 1 for i in range(len(texts)) if q in texts[i]), None) for q in golds]
            found = [x for x in ranks if x]
            rank = min(found) if found else None
            if found and rank <= 6:
                cause = "generation"
            elif found:
                cause = "rank_6_to_20"
            elif urls & {h["doc_url"] for h in hits}:
                cause = "chunk_missed"
            else:
                cause = "page_missed"
        out.append({"id": r["id"], "intent": r["intent"], "tier": r["tier"], "judge": r["judge"], "cause": cause, "best_rank": rank,
                    "abstained": r["abstained"], "question": r["question"][:140]})
        if n % 25 == 0:
            print(f"  {n}/{len(rows)}", flush=True)
    path = ROOT / "eval" / "reports" / "diagnosis_20261114.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"\nnon-correct, non-core answers: {len(out)}   -> {path}")
    print("overall:", dict(Counter(o["cause"] for o in out)))
    for intent in sorted({o["intent"] for o in out}):
        sub = [o for o in out if o["intent"] == intent]
        print(f"  {intent:<22} n={len(sub):<3} " + " ".join(f"{k}={v}" for k, v in Counter(o['cause'] for o in sub).most_common()))


if __name__ == "__main__":
    main()
