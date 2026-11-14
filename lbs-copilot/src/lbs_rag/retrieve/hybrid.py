"""Hybrid retrieval: BM25 + dense (Chroma) merged by reciprocal-rank fusion, optional rewrite / re-rank.

Public contract:
    retrieve(query, filters=None, k=8, *, mode="hybrid", rewrite=False, rerank=False) -> list[hit]
    hit = {chunk_id, doc_url, provider, api, text, score, fetched_at, effective_date, section}
"""
import re
from functools import lru_cache

from ..index.bm25 import BM25Index, load_chunks
from ..index.embed import embed_query
from ..index.vector_store import get_collection

RRF_K = 60
CANDIDATES = 30


@lru_cache(maxsize=1)
def _bm25() -> BM25Index:
    return BM25Index(load_chunks())


@lru_cache(maxsize=1)
def _col():
    return get_collection()


def _hit(c: dict, score: float) -> dict:
    return {k: c.get(k) for k in ("chunk_id", "doc_url", "provider", "api", "text", "fetched_at",
                                  "effective_date", "section", "kind")} | {"score": score}


def _passes(c: dict, filters: dict | None) -> bool:
    if not filters:
        return True
    return all(c.get(k) in (v if isinstance(v, (list, set, tuple)) else [v]) for k, v in filters.items())


def bm25_search(q: str, filters=None, n=CANDIDATES) -> list[dict]:
    pool = _bm25().search(q, k=n * 5 if filters else n)
    return [_hit(c, s) for c, s in pool if _passes(c, filters)][:n]


def dense_search(q: str, filters=None, n=CANDIDATES) -> list[dict]:
    where = None
    if filters:
        conds = [{k: {"$in": list(v) if isinstance(v, (list, set, tuple)) else [v]}} for k, v in filters.items()]
        where = conds[0] if len(conds) == 1 else {"$and": conds}
    r = _col().query(query_embeddings=[embed_query(q)], n_results=n, where=where,
                     include=["documents", "metadatas", "distances"])
    out = []
    for cid, doc, meta, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0]):
        out.append(_hit({**meta, "chunk_id": cid, "text": doc}, 1 - dist))
    return out


def rrf(rankings: list[list[dict]], k: int = RRF_K) -> list[dict]:
    fused: dict[str, dict] = {}
    for ranking in rankings:
        for rank, h in enumerate(ranking):
            e = fused.setdefault(h["chunk_id"], {**h, "score": 0.0})
            e["score"] += 1.0 / (k + rank + 1)
    return sorted(fused.values(), key=lambda h: -h["score"])


COVER_PROVIDERS = ["Ola Maps", "Google Maps Platform", "Mapbox", "OSRM", "HERE", "Mappls (MapmyIndia)"]
_CROSS = re.compile(r"\b(which|what|any|all|every)\b.{0,60}\b(apis?|providers?|vendors?|services?|options?|products?)\b|"
                    r"\bdo (all|any|the)\b.{0,40}\b(support|provide|offer|have)\b|\bwhich\b.{0,40}\b(support|give|offer|provide|allow|let)",
                    re.I | re.S)


def is_cross_provider(query: str, provs: list[str]) -> bool:
    """A question that names no provider but asks which ones can do something: the reference answers go provider by provider."""
    return not provs and bool(_CROSS.search(query))


def _cover(query: str, queries: list[str], hits: list[dict], k: int, extra: int = 2) -> list[dict]:
    """Make sure every provider that has a relevant page is represented: add the best chunk of each provider missing from the top-k.
    The result may be up to `extra` chunks longer than k (a larger prompt only when the question spans providers)."""
    have = {h["provider"] for h in hits[:k]}
    add = []
    for p in COVER_PROVIDERS:
        if p in have:
            continue
        r = rrf([bm25_search(q, {"provider": p}, 10) for q in queries] + [dense_search(q, {"provider": p}, 10) for q in queries])
        if r:
            add.append(r[0])
    add.sort(key=lambda h: -h["score"])
    return hits[:k] + add[:extra + 2]


def retrieve(query: str, filters: dict | None = None, k: int = 8, *, mode: str = "hybrid",
             rewrite: bool = False, rerank: bool = False, route: bool = False, cover: bool = False) -> list[dict]:
    """route=True: if the question names provider(s), add searches restricted to those providers' chunks (extra RRF votes),
    so e.g. an Ola question is not crowded out by Google pages. Unfiltered searches are kept, so nothing is excluded.
    cover=True: a cross-provider question ("which APIs support trucks?") also gets the best chunk of each provider not already present."""
    queries = [query]
    if rewrite:
        from .query_analyzer import rewrite as do_rewrite
        queries = do_rewrite(query)
    provs: list[str] = []
    if route and not filters:
        from .query_analyzer import detect
        provs = detect(query)["providers"]
    rankings = []
    for q in queries:
        if mode in ("hybrid", "bm25"):
            rankings.append(bm25_search(q, filters))
            if provs:
                rankings.append(bm25_search(q, {"provider": provs}))
        if mode in ("hybrid", "dense"):
            rankings.append(dense_search(q, filters))
            if provs:
                rankings.append(dense_search(q, {"provider": provs}))
    hits = rrf(rankings)
    if rerank:
        from .rerank import rerank as do_rerank
        return do_rerank(query, hits[:CANDIDATES], top_k=k)
    if cover and not filters and mode == "hybrid":
        from .query_analyzer import detect
        if is_cross_provider(query, provs or detect(query)["providers"]):
            return _cover(query, queries, hits, k)
    return hits[:k]
