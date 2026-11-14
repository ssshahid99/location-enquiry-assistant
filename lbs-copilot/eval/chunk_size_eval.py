"""Activity 8 evidence: recall@5 for several chunk sizes, on the DEV split (never the frozen core).

Each size re-chunks the raw snapshot (the same parser and chunker as the real ingest, only the token budget changes), embeds the chunks with the
shared embedding endpoint (cached on disk), and scores BM25, dense and hybrid (RRF) retrieval with the strict gold-quote recall@5 of
eval/chunk_recall.py. Nothing in the real index is touched. Query rewriting is off (a model call).
Pass (sheet): the chosen chunk size has the highest dev recall@5.   Usage: python eval/chunk_size_eval.py [--sizes 240,360,480]
"""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402
from chunk_recall import norm, quotes  # noqa: E402
from load_gt import load_items  # noqa: E402

from lbs_rag.index.bm25 import BM25Index  # noqa: E402
from lbs_rag.index.embed import embed_docs, embed_query  # noqa: E402
from lbs_rag.ingest import chunk as chunk_mod  # noqa: E402
from lbs_rag.ingest.fetch import slug  # noqa: E402
from lbs_rag.ingest.run_ingest import parse_source  # noqa: E402
from lbs_rag.retrieve.hybrid import rrf  # noqa: E402

CACHE = ROOT / "eval" / "reports" / "chunk_size_cache"


def chunk_all(size: int) -> list[dict]:
    chunk_mod.EMBED_TOKENS = size
    raw_root = ROOT / "data" / "raw"
    rawdir = raw_root / sorted(p.name for p in raw_root.iterdir() if p.is_dir())[-1]
    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    out = []
    for s in sources:
        ext = {"stackoverflow": "json", "openapi": "yaml"}.get(s["kind"], "html")
        path = rawdir / f"{slug(s['url'])}.{ext}"
        if not path.exists():
            continue
        meta = {**s, "fetched_at": "x", "effective_date": None}
        title, sections, meta["effective_date"] = parse_source(s, path.read_text(encoding="utf-8"))
        out += chunk_mod.chunk_document(meta, title or s["url"], sections)
    return out


def vectors(size: int, chunks: list[dict]) -> np.ndarray:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(("|".join(c["chunk_id"] + str(len(c["text"])) for c in chunks)).encode()).hexdigest()[:12]
    f = CACHE / f"vec_{size}_{key}.npy"
    if f.exists():
        return np.load(f)
    v = np.array(embed_docs([c["text"] for c in chunks]), dtype="float32")
    np.save(f, v)
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="240,360,480")
    ap.add_argument("--split", default="working")
    a = ap.parse_args()
    cit = pd.read_csv(ROOT / "data" / "ground_truth" / "gt_v3_citations.csv")
    cit = cit[cit["quote_or_summary"] == "verbatim"]
    byid = {i: g for i, g in cit.groupby("ID")}
    sizes = [int(x) for x in a.sizes.split(",")]
    base = chunk_all(max(sizes))
    doc_text: dict[str, str] = defaultdict(str)
    for c in base:
        doc_text[c["doc_url"]] += " " + norm(c["text"])
    gold = []
    for it in load_items(a.split):
        g = byid.get(it["ID"])
        if g is None:
            continue
        for _, r in g.iterrows():
            for q in quotes(r["span"]):
                if q in doc_text.get(r["doc_url"], ""):
                    gold.append((it["Question"], q))
    print(f"dev split '{a.split}': {len({q for q, _ in gold})} questions, {len(gold)} reachable gold quotes\n")
    qvec = {q: np.array(embed_query(q), dtype="float32") for q in dict.fromkeys(q for q, _ in gold)}
    print(f"{'tokens':>6} {'chunks':>7} {'median':>7} {'bm25':>7} {'dense':>7} {'hybrid':>7}")
    best = {}
    for size in sizes:
        chunks = chunk_all(size)
        toks = sorted(c["n_tokens"] for c in chunks)
        vec = vectors(size, chunks)
        bm = BM25Index(chunks)
        norms = [norm(c["text"]) for c in chunks]
        pos = {c["chunk_id"]: i for i, c in enumerate(chunks)}
        found = {"bm25": 0, "dense": 0, "hybrid": 0}
        cache: dict[str, dict] = {}
        for question, q in gold:
            if question not in cache:
                b = [{"chunk_id": pos[c["chunk_id"]], "score": s} for c, s in bm.search(question, 30)]
                sims = vec @ qvec[question]
                d = [{"chunk_id": int(i), "score": float(sims[i])} for i in np.argsort(-sims)[:30]]
                cache[question] = {"bm25": [h["chunk_id"] for h in b], "dense": [h["chunk_id"] for h in d],
                                   "hybrid": [h["chunk_id"] for h in rrf([b, d])]}
            for name, ids in cache[question].items():
                found[name] += any(q in norms[i] for i in ids[:5])
        n = len(gold)
        best[size] = found["hybrid"] / n
        print(f"{size:>6} {len(chunks):>7} {toks[len(toks) // 2]:>7} {found['bm25'] / n:>7.3f} {found['dense'] / n:>7.3f} {found['hybrid'] / n:>7.3f}")
    top = max(best, key=best.get)
    print(f"\nhighest dev hybrid recall@5: {top} tokens ({best[top]:.3f}); the index in use is built with a "
          f"{int(__import__('os').environ.get('LBS_EMBED_TOKENS', 480))}-token budget")
    (ROOT / "eval" / "reports" / "chunk_size_eval.json").write_text(json.dumps({"hybrid_recall_at_5": best}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
