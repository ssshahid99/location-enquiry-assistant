"""Cross-encoder re-ranker (bge-reranker-base, local)."""
from functools import lru_cache

RERANK_MODEL = "BAAI/bge-reranker-base"


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(RERANK_MODEL, max_length=512)


def rerank(query: str, hits: list[dict], top_k: int = 8) -> list[dict]:
    if not hits:
        return hits
    scores = _model().predict([(query, h["text"]) for h in hits], batch_size=16)
    ranked = sorted(zip(hits, scores), key=lambda x: -x[1])[:top_k]
    return [{**h, "score": float(s)} for h, s in ranked]
