"""In-memory BM25 over chunks.jsonl (rebuilt on load; ~1k chunks builds in well under a second)."""
import json
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[3]
CHUNKS = ROOT / "data" / "processed" / "chunks.jsonl"
# keep numbers/units/currency intact: "0.199", "100,000", "$0.30" matter for pricing questions
_TOK = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*|[₹$€]")


def tokenize(s: str) -> list[str]:
    return _TOK.findall(s.lower())


def load_chunks(path: Path = CHUNKS) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class BM25Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])

    def search(self, query: str, k: int = 20) -> list[tuple[dict, float]]:
        scores = self.bm25.get_scores(tokenize(query))
        top = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [(self.chunks[i], float(scores[i])) for i in top if scores[i] > 0]
