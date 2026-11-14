import json
from pathlib import Path

import pytest

from lbs_rag.ingest import chunk as C

LIMIT = 512
CHUNKS = Path(__file__).resolve().parents[1] / "data" / "processed" / "chunks.jsonl"
META = {"url": "https://example.com/x", "provider": "Test Provider", "api": "Directions", "kind": "docs", "licence": "x", "fetched_at": "2026-11-14"}


def sec(text, name="S"):
    return [{"section": name, "text": text}]


def test_no_chunk_exceeds_the_endpoint_limit_even_for_number_heavy_text():
    nasty = " ".join(f"https://example.com/a/{i}?key=abcdef{i}&lat=12.9716{i}&lng=77.5946{i}" for i in range(400))
    chunks = C.chunk_document(META, "T", sec(nasty))
    assert len(chunks) > 1 and max(c["n_tokens"] for c in chunks) <= C.EMBED_TOKENS


def test_header_is_counted_in_the_budget():
    long_title = "A very long document title " * 12
    chunks = C.chunk_document(META, long_title, sec("word " * 900, name="Long section heading " * 5))
    assert max(C.model_tokens(c["text"]) for c in chunks) <= C.EMBED_TOKENS


def test_hard_split_never_alters_the_text_and_keeps_quotes_findable():
    p = "Distance Matrix allows MAX 25 origins 100 elements. " * 200 + "UNIQUE-QUOTE-Alpha 12345.678 Beta"
    pieces = C._hard_split(p, 120)
    assert all(C.ntok(x) <= 120 for x in pieces)
    assert "UNIQUE-QUOTE-Alpha 12345.678 Beta" in " ".join(pieces)                 # case and digits preserved (no lower-casing / detokenising)
    assert pieces[0] == p[:len(pieces[0])]                                          # pieces are exact substrings of the original


def test_small_sections_are_a_single_chunk():
    chunks = C.chunk_document(META, "T", sec("Short callout text that fits easily in one chunk of text for the test."))
    assert len(chunks) == 1 and chunks[0]["n_tokens"] < 100


@pytest.mark.skipif(not CHUNKS.exists(), reason="no local index")
def test_every_chunk_in_the_current_index_fits():
    big = []
    for line in CHUNKS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            if C.model_tokens(c["text"]) > LIMIT:
                big.append((c["doc_url"][-40:], C.model_tokens(c["text"])))
    assert not big, f"{len(big)} chunks exceed {LIMIT} model tokens, e.g. {big[:3]} (re-run the ingest and re-index)"
