"""Retrieval ablation on the eval split (holdout stays untouched).

Metric (doc-level, because gold labels are source URLs): hit@k = any gold URL among top-k docs;
recall@k = fraction of gold URLs found; MRR over first gold hit.
Usage: python eval/retrieval_eval.py [--n 100] [--configs bm25,dense,hybrid,hybrid+rw,hybrid+rr,hybrid+rw+rr]
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

from lbs_rag.retrieve.hybrid import retrieve  # noqa: E402

CONFIGS = {
    "bm25": dict(mode="bm25"),
    "dense": dict(mode="dense"),
    "hybrid": dict(mode="hybrid"),
    "hybrid+rw": dict(mode="hybrid", rewrite=True),
    "hybrid+rr": dict(mode="hybrid", rerank=True),
    "hybrid+rw+rr": dict(mode="hybrid", rewrite=True, rerank=True),
}


def evaluate(items: list[dict], cfg: dict, ks=(1, 3, 5, 8)) -> dict:
    hit = {k: 0 for k in ks}
    rec = {k: 0.0 for k in ks}
    mrr = 0.0
    n = 0
    for it in items:
        gold = set(it["gold_urls"])
        if not gold:
            continue
        hits = retrieve(it["Question"], k=max(ks), **cfg)
        urls: list[str] = []
        for h in hits:
            if h["doc_url"] not in urls:
                urls.append(h["doc_url"])
        n += 1
        for k in ks:
            found = gold & set(urls[:k])
            hit[k] += bool(found)
            rec[k] += len(found) / len(gold)
        first = next((i for i, u in enumerate(urls) if u in gold), None)
        mrr += 1 / (first + 1) if first is not None else 0
    return {"n": n, **{f"hit@{k}": hit[k] / n for k in ks}, **{f"recall@{k}": rec[k] / n for k in ks}, "mrr": mrr / n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="limit items (0 = all of eval split)")
    ap.add_argument("--configs", default="bm25,dense,hybrid")
    a = ap.parse_args()
    items = load_items("eval")
    if a.n:
        items = items[:a.n]
    print(f"eval split items: {len(items)}")
    print(f"{'config':<14}{'n':>5}{'hit@1':>8}{'hit@3':>8}{'hit@5':>8}{'hit@8':>8}{'rec@8':>8}{'mrr':>8}{'sec':>7}")
    for name in a.configs.split(","):
        t = time.time()
        m = evaluate(items, CONFIGS[name])
        print(f"{name:<14}{m['n']:>5}{m['hit@1']:>8.3f}{m['hit@3']:>8.3f}{m['hit@5']:>8.3f}{m['hit@8']:>8.3f}"
              f"{m['recall@8']:>8.3f}{m['mrr']:>8.3f}{time.time() - t:>7.0f}")


if __name__ == "__main__":
    main()
