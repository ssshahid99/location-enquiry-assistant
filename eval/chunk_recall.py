"""Chunk-level retrieval eval: does the *cited passage itself* reach the model?

The doc-level metric (retrieval_eval.py) said ~94% but only asks whether the right page is somewhere in the results.
Here the gold is each verbatim quote in gt_citations.csv (quote_or_summary == 'verbatim'; the `span` column holds the text):
a quote counts as found if its normalised text appears in the concatenated top-k chunks. Quotes that are not even on the
fetched page (paraphrased in the ground truth) are reported separately as 'unreachable' and excluded from recall.
Usage: python eval/chunk_recall.py [--split eval] [--k 6,10,20] [--configs hybrid,bm25,dense] [--nonprice]
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

import pandas as pd  # noqa: E402
from load_gt import load_items  # noqa: E402

from lbs_rag.index.bm25 import load_chunks  # noqa: E402
from lbs_rag.retrieve.hybrid import retrieve  # noqa: E402

CONFIGS = {"bm25": dict(mode="bm25"), "dense": dict(mode="dense"), "hybrid": dict(mode="hybrid"),
           "hybrid+rr": dict(mode="hybrid", rerank=True), "hybrid+rw": dict(mode="hybrid", rewrite=True),
           "hybrid+route": dict(mode="hybrid", route=True), "hybrid+rw+route": dict(mode="hybrid", rewrite=True, route=True)}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", str(s).lower().replace("*", "").replace("`", ""))).strip()


def quotes(span: str) -> list[str]:
    qs = re.findall(r'["“]([^"”]{18,})["”]', str(span))
    return [norm(q) for q in qs] or ([norm(span)] if len(str(span)) > 25 else [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="eval")
    ap.add_argument("--k", default="6,10,20")
    ap.add_argument("--configs", default="hybrid")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--nonprice", action="store_true", help="skip pricing intents (the calculator handles those)")
    a = ap.parse_args()
    ks = [int(x) for x in a.k.split(",")]
    gdir = ROOT / "data" / "ground_truth"
    cit = pd.read_csv(gdir / ("gt_v3_citations.csv" if (gdir / "gt_v3_citations.csv").exists() else "gt_citations.csv"))
    cit = cit[cit["quote_or_summary"] == "verbatim"]
    by_doc = defaultdict(list)
    for c in load_chunks():
        by_doc[c["doc_url"]].append(norm(c["text"]))
    doc_text = {u: " ".join(v) for u, v in by_doc.items()}
    items = load_items(a.split)
    if a.nonprice:
        items = [i for i in items if i["intent"] not in ("pricing_quote", "pricing_comparison", "pricing_estimate")]
    byid = {i: g for i, g in cit.groupby("ID")}
    items = [i for i in items if i["ID"] in byid]
    if a.n:
        items = items[::max(1, len(items) // a.n)][:a.n]
    gold = []                                            # (item, quote)
    unreachable = 0
    for it in items:
        for _, r in byid[it["ID"]].iterrows():
            for q in quotes(r["span"]):
                if q in doc_text.get(r["doc_url"], ""):
                    gold.append((it, q))
                else:
                    unreachable += 1
    print(f"items={len(items)} reachable gold quotes={len(gold)} (unreachable/paraphrased: {unreachable})")
    for name in a.configs.split(","):
        found = {k: 0 for k in ks}
        cache: dict[str, list] = {}
        for it, q in gold:
            if it["ID"] not in cache:
                cache[it["ID"]] = [norm(h["text"]) for h in retrieve(it["Question"], k=max(ks), **CONFIGS[name])]
            hits = cache[it["ID"]]
            for k in ks:
                found[k] += q in " ".join(hits[:k])
        print(f"{name:<10} " + "  ".join(f"quote@{k}={found[k] / len(gold):.3f}" for k in ks))


if __name__ == "__main__":
    main()
