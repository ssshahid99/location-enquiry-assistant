"""Local embeddings (sentence-transformers); the project gateway has no /embeddings endpoint."""
from functools import lru_cache

from config.settings import settings

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge convention


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.embed_model)


def embed_docs(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    return _model().encode(texts, batch_size=batch_size, normalize_embeddings=True,
                           show_progress_bar=True).tolist()


def embed_query(q: str) -> list[float]:
    return _model().encode([QUERY_PREFIX + q], normalize_embeddings=True)[0].tolist()
