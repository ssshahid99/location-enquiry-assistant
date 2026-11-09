"""Embed data/processed/chunks.jsonl into Chroma. BM25 needs no build step (see bm25.py)."""
import time

from .bm25 import BM25Index, load_chunks
from .embed import embed_docs
from .vector_store import add_chunks, reset_collection


def main() -> None:
    chunks = load_chunks()
    t = time.time()
    vecs = embed_docs([c["text"] for c in chunks])
    col = reset_collection()
    add_chunks(col, chunks, vecs)
    print(f"chroma: {col.count()} chunks, dim {len(vecs[0])}, {time.time() - t:.0f}s")
    idx = BM25Index(chunks)
    print("bm25 sanity:", [(c["provider"], c["section"][:30]) for c, _ in idx.search("Ola Distance Matrix price per pair", 3)])


if __name__ == "__main__":
    main()
