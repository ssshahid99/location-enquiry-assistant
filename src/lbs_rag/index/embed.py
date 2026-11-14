"""Embeddings for indexing and search, with two backends (EMBED_BACKEND in .env):

  endpoint  the shared institution endpoint (bge-small-en-v1.5, 384-d, POST /v1/embeddings, at most 16 texts per request, 512 tokens
            per text). REQUIRED for the final system. Retries with back-off when the server reports capacity (429/503) or drops the
            connection; every vector is checked (count, order by `index`, dimension) and L2-normalised.
  local     sentence-transformers bge-base-en-v1.5 on this machine: a development fallback only. Its vectors (768-d) are NOT interchangeable
            with the endpoint's (384-d): the index must be rebuilt when the backend changes, and `backend_info()` is checked at query time.

Queries use the bge instruction prefix, documents do not (same convention for both models).
"""
import math
import time
from functools import lru_cache

from config.settings import EVENT_HOOKS, settings

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge convention
ENDPOINT_BATCH = 16
ENDPOINT_DIM = 384
_RETRY_STATUS = {429, 500, 502, 503, 504}


class EmbeddingError(RuntimeError):
    pass


def backend() -> str:
    return (settings.embed_backend or "local").strip().lower()


def backend_info() -> dict:
    """Which model produced (or will produce) the vectors; stored with the index so a mismatch is caught instead of silently wrong."""
    if backend() == "endpoint":
        return {"backend": "endpoint", "model": settings.embed_model_endpoint, "dim": ENDPOINT_DIM}
    return {"backend": "local", "model": settings.embed_model, "dim": 768}


# ---------------------------------------------------------------------------------------------- endpoint
@lru_cache(maxsize=1)
def _http():
    import httpx
    if not (settings.embed_base_url and settings.embed_api_key):
        raise EmbeddingError("EMBED_BASE_URL / EMBED_API_KEY are not set in .env")
    verify = settings.embed_ca_bundle or True
    return httpx.Client(base_url=settings.embed_base_url.rstrip("/"), verify=verify, timeout=60, event_hooks=EVENT_HOOKS,
                        headers={"Authorization": f"Bearer {settings.embed_api_key}", "Content-Type": "application/json"})


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _post_batch(texts: list[str], *, retries: int = 6, sleep=time.sleep) -> list[list[float]]:
    """One request of up to 16 texts. Returns vectors in input order. Capacity errors are retried with back-off; anything else raises."""
    import httpx
    last = None
    for attempt in range(retries + 1):
        try:
            r = _http().post("/embeddings", json={"model": settings.embed_model_endpoint, "input": texts})
        except (httpx.TransportError, httpx.TimeoutException) as e:      # dropped connection, timeout
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200:
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                vecs = [d["embedding"] for d in data]
                if len(vecs) != len(texts):
                    raise EmbeddingError(f"asked for {len(texts)} vectors, got {len(vecs)}")
                if any(len(v) != ENDPOINT_DIM for v in vecs):
                    raise EmbeddingError(f"expected {ENDPOINT_DIM}-d vectors, got {len(vecs[0])}")
                return [_normalise(v) for v in vecs]
            if r.status_code not in _RETRY_STATUS:
                raise EmbeddingError(f"embedding endpoint returned HTTP {r.status_code}: {r.text[:200]}")
            last = f"HTTP {r.status_code}"
        if attempt < retries:
            sleep(min(30, 1.5 * 2 ** attempt))                           # wait briefly and retry the batch (capacity error)
    raise EmbeddingError(f"embedding endpoint unavailable after {retries + 1} attempts ({last})")


def _endpoint_embed(texts: list[str], progress: bool = False) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), ENDPOINT_BATCH):
        out += _post_batch(texts[i:i + ENDPOINT_BATCH])
        if progress and (i // ENDPOINT_BATCH) % 10 == 0:
            print(f"  embedded {min(i + ENDPOINT_BATCH, len(texts))}/{len(texts)}", flush=True)
    return out


# ---------------------------------------------------------------------------------------------- local
@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.embed_model)


# ---------------------------------------------------------------------------------------------- public API
def embed_docs(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    if backend() == "endpoint":
        return _endpoint_embed(texts, progress=len(texts) > 64)
    return _model().encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=True).tolist()


@lru_cache(maxsize=4096)
def _cached_endpoint_query(q: str) -> tuple[float, ...]:
    """The same query text is embedded several times per question (guard gate, rewrite variants, provider-aware searches): one request each is
    wasteful on a shared endpoint, so repeats are served from memory."""
    return tuple(_endpoint_embed([QUERY_PREFIX + q])[0])


def embed_query(q: str) -> list[float]:
    if backend() == "endpoint":
        return list(_cached_endpoint_query(q))
    return _model().encode([QUERY_PREFIX + q], normalize_embeddings=True)[0].tolist()
