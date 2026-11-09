"""Chroma persistent store (cosine). Metadata is kept for filtering and citations."""
from pathlib import Path

import chromadb

ROOT = Path(__file__).resolve().parents[3]
COLLECTION = "lbs_chunks"
META_KEYS = ["doc_url", "provider", "api", "kind", "licence", "title", "section", "fetched_at", "effective_date"]


def client():
    return chromadb.PersistentClient(path=str(ROOT / "data" / "chroma"))


def reset_collection():
    c = client()
    try:
        c.delete_collection(COLLECTION)
    except Exception:  # noqa: BLE001 - first build
        pass
    return c.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def get_collection():
    return client().get_collection(COLLECTION)


def add_chunks(col, chunks: list[dict], vectors: list[list[float]], batch: int = 256) -> None:
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        col.add(
            ids=[c["chunk_id"] for c in part],
            embeddings=vectors[i:i + batch],
            documents=[c["text"] for c in part],
            metadatas=[{k: (c.get(k) or "") for k in META_KEYS} for c in part],
        )
