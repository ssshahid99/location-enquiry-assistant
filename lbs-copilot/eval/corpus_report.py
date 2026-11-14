"""Activity 7 evidence: corpus size, metadata completeness (source URL, retrieval date, licence, doc type) and near-duplicate rate.

Near-duplicates: MinHash (128 permutations, 5-word shingles) estimates the Jaccard similarity of every pair of documents; pairs at or above the
threshold are checked against the exact Jaccard. datasketch is not installed in this environment, so MinHash is implemented here in plain Python
(universal hashing over a 61-bit prime); the exact Jaccard of every flagged pair is reported next to the estimate.
Pass (sheet): 500-2,000 docs, 100% have metadata, near-duplicate rate < 2%.   Usage: python eval/corpus_report.py [--threshold 0.8]
"""
import argparse
import hashlib
import itertools
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import yaml  # noqa: E402

from lbs_rag.index.bm25 import load_chunks  # noqa: E402

PRIME = (1 << 61) - 1
NPERM = 128


def shingles(text: str, n: int = 5) -> set[int]:
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {int.from_bytes(hashlib.blake2b(" ".join(w[i:i + n]).encode(), digest_size=8).digest(), "big") for i in range(max(1, len(w) - n + 1))}


def minhash(sh: set[int], coeffs: list[tuple[int, int]]) -> list[int]:
    return [min((a * x + b) % PRIME for x in sh) for a, b in coeffs]


def est(a: list[int], b: list[int]) -> float:
    return sum(x == y for x, y in zip(a, b)) / len(a)


def exact(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.8)
    a = ap.parse_args()
    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    chunks = load_chunks()
    docs: dict[str, list[str]] = defaultdict(list)
    meta: dict[str, dict] = {}
    for c in chunks:
        docs[c["doc_url"]].append(c["text"])
        meta[c["doc_url"]] = c
    n_src = len(sources)
    print(f"sources configured {n_src}; documents in the index {len(docs)}; chunks {len(chunks)}")
    need = {"url": lambda u, s, c: bool(u), "retrieval date (fetched_at)": lambda u, s, c: bool(c.get("fetched_at")),
            "licence": lambda u, s, c: bool(s.get("licence")), "doc type (kind)": lambda u, s, c: bool(s.get("kind") and c.get("kind"))}
    by_url = {s["url"]: s for s in sources}
    complete = 0
    for u, c in meta.items():
        s = by_url.get(u, {})
        complete += all(f(u, s, c) for f in need.values())
    for name, f in need.items():
        print(f"  {name:<28} {sum(f(u, by_url.get(u, {}), c) for u, c in meta.items())}/{len(meta)}")
    print(f"documents with ALL metadata {complete}/{len(meta)} = {complete / len(meta):.1%}   (target 100%)")
    rnd = random.Random(7)
    coeffs = [(rnd.randrange(1, PRIME), rnd.randrange(0, PRIME)) for _ in range(NPERM)]
    sh = {u: shingles(" ".join(t)) for u, t in docs.items()}
    mh = {u: minhash(s, coeffs) for u, s in sh.items()}
    pairs = []
    for u, v in itertools.combinations(sorted(docs), 2):
        e = est(mh[u], mh[v])
        if e >= a.threshold - 0.1:                               # a little slack for the estimator, then confirmed exactly
            ex = exact(sh[u], sh[v])
            if ex >= a.threshold:
                pairs.append((ex, e, u, v))
    dup_docs = {v for _, _, u, v in pairs}                       # the later document of each pair is the duplicate
    rate = len(dup_docs) / len(docs)
    print(f"near-duplicate pairs (exact Jaccard >= {a.threshold}): {len(pairs)}; documents that duplicate an earlier one: {len(dup_docs)} = {rate:.1%}   (target < 2%)")
    for ex, e, u, v in sorted(pairs, reverse=True)[:8]:
        print(f"  J={ex:.2f} (minhash {e:.2f})  {u}  ~  {v}")
    rep = {"documents": len(docs), "sources": n_src, "chunks": len(chunks), "metadata_complete": complete, "near_duplicate_pairs": len(pairs),
           "near_duplicate_docs": sorted(dup_docs), "near_duplicate_rate": rate,
           "sheet_range_500_2000_docs": 500 <= len(docs) <= 2000}
    (ROOT / "eval" / "reports").mkdir(exist_ok=True)
    (ROOT / "eval" / "reports" / "corpus_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(f"documents in the 500-2,000 range of the sheet: {rep['sheet_range_500_2000_docs']} ({len(docs)})")


if __name__ == "__main__":
    main()
