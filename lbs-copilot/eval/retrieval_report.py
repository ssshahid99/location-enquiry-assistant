"""Activities 9 and 10 evidence: BM25, dense, hybrid and hybrid + cross-encoder re-rank, scored against the gold cited spans.

Gold = the verbatim quotes of gt_v3_citations.csv that are actually on the fetched page (paraphrased gold is excluded: retrieval cannot find it).
A retrieved chunk is relevant to a question if it contains one of that question's gold quotes. Reported per config:
  quote recall@1/5/10   share of gold quotes that appear in the top-k chunks
  hit@5                 share of questions with at least one relevant chunk in the top 5
  MRR                   mean reciprocal rank of the first relevant chunk (0 if none in the top 20)
  nDCG@10               binary relevance, standard log2 discount
Query rewriting is OFF here so the table measures the retrievers themselves (rewriting is a model call). Per bucket for the hybrid config.
Usage: python eval/retrieval_report.py [--split working|core] [--configs bm25,dense,hybrid,hybrid+rr]
"""
import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

import pandas as pd  # noqa: E402
from chunk_recall import norm, quotes  # noqa: E402
from load_gt import load_items  # noqa: E402

from lbs_rag.index.bm25 import load_chunks  # noqa: E402
from lbs_rag.retrieve.hybrid import retrieve  # noqa: E402

CONF = {"bm25": dict(mode="bm25"), "dense": dict(mode="dense"), "hybrid": dict(mode="hybrid"), "hybrid+rr": dict(mode="hybrid", rerank=True)}


def ndcg10(rel: list[int], n_rel_total: int) -> float:
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel[:10]))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(n_rel_total, 10)))
    return dcg / ideal if ideal else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="working")
    ap.add_argument("--configs", default="bm25,dense,hybrid,hybrid+rr")
    a = ap.parse_args()
    cit = pd.read_csv(ROOT / "data" / "ground_truth" / "gt_v3_citations.csv")
    cit = cit[cit["quote_or_summary"] == "verbatim"]
    byid = {i: g for i, g in cit.groupby("ID")}
    doc_text: dict[str, str] = defaultdict(str)
    for c in load_chunks():
        doc_text[c["doc_url"]] += " " + norm(c["text"])
    items = []
    for it in load_items(a.split):
        g = byid.get(it["ID"])
        if g is None:
            continue
        qs = [q for _, r in g.iterrows() for q in quotes(r["span"]) if q in doc_text.get(r["doc_url"], "")]
        if qs:
            items.append((it, list(dict.fromkeys(qs))))
    print(f"split={a.split}: {len(items)} questions with {sum(len(q) for _, q in items)} reachable gold quotes (paraphrased gold excluded)\n")
    print(f"{'config':<11} {'recall@1':>9} {'recall@5':>9} {'recall@10':>10} {'hit@5':>7} {'MRR':>6} {'nDCG@10':>8}")
    per_bucket: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    res = {}
    for name in a.configs.split(","):
        rec = {1: 0, 5: 0, 10: 0}
        total = hit5 = 0
        mrr, nd = [], []
        for it, qs in items:
            hits = [norm(h["text"]) for h in retrieve(it["Question"], k=20, **CONF[name])]
            rel = [int(any(q in t for q in qs)) for t in hits]
            for k in rec:
                rec[k] += sum(any(q in t for t in hits[:k]) for q in qs)
            total += len(qs)
            hit5 += any(rel[:5])
            rr = next((1 / (i + 1) for i, r in enumerate(rel) if r), 0.0)
            mrr.append(rr)
            nd.append(ndcg10(rel, max(1, sum(rel))))
            if name == "hybrid":
                b = str(it.get("bucket") or "(none)")
                per_bucket[b]["hit5"].append(any(rel[:5]))
                per_bucket[b]["mrr"].append(rr)
        n = len(items)
        res[name] = (rec[5] / total, hit5 / n)
        print(f"{name:<11} {rec[1] / total:>9.3f} {rec[5] / total:>9.3f} {rec[10] / total:>10.3f} {hit5 / n:>7.3f} {sum(mrr) / n:>6.3f} {sum(nd) / n:>8.3f}")
    if {"bm25", "dense", "hybrid", "hybrid+rr"} <= set(res):
        best = max(res["bm25"][0], res["dense"][0])
        print(f"\nActivity 9 pass condition: hybrid+rerank recall@5 >= best single retriever + 5 points:  {res['hybrid+rr'][0]:.3f} vs {best:.3f} + 0.05 -> "
              f"{'PASS' if res['hybrid+rr'][0] >= best + 0.05 else 'FAIL'}")
    if per_bucket:
        print("\nhybrid by bucket (hit@5, MRR):")
        for b, d in sorted(per_bucket.items()):
            print(f"  {b:<18} n={len(d['hit5']):<3} hit@5 {sum(d['hit5']) / len(d['hit5']):.2f}  MRR {sum(d['mrr']) / len(d['mrr']):.2f}")


if __name__ == "__main__":
    main()
