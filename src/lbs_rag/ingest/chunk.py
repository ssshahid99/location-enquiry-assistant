"""Section -> chunks that FIT THE EMBEDDING MODEL, with metadata. Every chunk carries provenance for citations.

The embedding endpoint (bge-small-en-v1.5, required by the institution) accepts at most 512 model tokens per text. Those are BERT
WordPiece tokens, which split numbers, URLs and code into many more pieces than the GPT tokenizer we used before (tiktoken, 450):
that is how 51 of 1,217 chunks reached up to 905 model tokens and were silently truncated. The header line ("Provider | Title > Section")
is part of the embedded text, so it is counted too.

Budget per chunk: EMBED_TOKENS (default 480, a safety margin under 512) minus the 2 special tokens ([CLS], [SEP]) minus the header.
bge-small and bge-base share the same WordPiece vocabulary, so one tokenizer counts for both.
Oversized single blocks are cut at TOKEN boundaries using character offsets, so the text itself is never altered (quotes stay verbatim).
"""
import hashlib
import os
import re
from functools import lru_cache

TOKENIZER_NAME = os.environ.get("LBS_TOKENIZER", "BAAI/bge-base-en-v1.5")      # same vocabulary as bge-small-en-v1.5
EMBED_TOKENS = int(os.environ.get("LBS_EMBED_TOKENS", "480"))                  # whole embedded text incl. header and [CLS]/[SEP]
OVERLAP = int(os.environ.get("LBS_CHUNK_OVERLAP", "60"))
MIN_TOKENS = 6      # keep short callouts ("As of October 2026 ... no longer ...") - they are often the answer
SPECIAL = 2         # [CLS] and [SEP]


@lru_cache(maxsize=1)
def _tok():
    from transformers import AutoTokenizer
    t = AutoTokenizer.from_pretrained(TOKENIZER_NAME)
    t.model_max_length = 10**9                                                  # we count; we do not want truncation warnings
    return t


def ntok(s: str) -> int:
    """Model tokens of `s` (without [CLS]/[SEP])."""
    return len(_tok()(s, add_special_tokens=False)["input_ids"])


def model_tokens(text: str) -> int:
    """Tokens the embedding model really sees for this text, including [CLS] and [SEP]."""
    return ntok(text) + SPECIAL


def _hard_split(p: str, budget: int) -> list[str]:
    """Cut one oversized block into windows of at most `budget` model tokens (with OVERLAP), at token boundaries, text unchanged."""
    enc = _tok()(p, add_special_tokens=False, return_offsets_mapping=True)
    offs = enc["offset_mapping"]
    n, step, out = len(offs), max(1, budget - OVERLAP), []
    for i in range(0, n, step):
        j = min(i + budget, n)
        out.append(p[offs[i][0]:offs[j - 1][1]])
        if j >= n:
            break
    return out


def _split_text(text: str, budget: int) -> list[str]:
    """Pack paragraphs up to `budget` model tokens; split oversized blocks on lines, then (last resort) at token boundaries."""
    parts = []
    for p in re.split(r"\n\s*\n", text):
        p = p.strip()
        if not p:
            continue
        # oversized block (OpenAPI params, tables): split on lines so rows/params stay whole
        parts += [l for l in p.split("\n") if l.strip()] if ntok(p) > budget else [p]
    out, cur, cur_n = [], [], 0
    for p in parts:
        n = ntok(p)
        if n > budget:
            if cur:
                out.append("\n\n".join(cur)); cur, cur_n = [], 0
            out += _hard_split(p, budget)
            continue
        if cur_n + n > budget and cur:
            out.append("\n\n".join(cur))
            tail = cur[-1] if ntok(cur[-1]) <= OVERLAP else ""
            cur, cur_n = ([tail] if tail else []), (ntok(tail) if tail else 0)
        cur.append(p)
        cur_n += n
    if cur:
        out.append("\n\n".join(cur))
    return out


def _fit(header: str, piece: str, budget: int) -> list[str]:
    """Guarantee: header + piece is at most EMBED_TOKENS model tokens as the embedding model really counts it. Joining paragraphs or
    cutting a block can tokenise slightly differently from the sum of the parts, so re-split with a smaller budget until it fits."""
    if model_tokens(f"{header}\n\n{piece}") <= EMBED_TOKENS or budget <= 32:
        return [piece]
    out = []
    for sub in _split_text(piece, int(budget * 0.85)):
        out += _fit(header, sub, int(budget * 0.85))
    return out


def chunk_document(meta: dict, title: str, sections: list[dict]) -> list[dict]:
    """meta: url, provider, api, kind, licence, fetched_at, effective_date, ..."""
    chunks = []
    for sec in sections:
        header = f"{meta['provider']} | {title} > {sec['section']}" if sec["section"] != title else f"{meta['provider']} | {title}"
        budget = max(64, EMBED_TOKENS - SPECIAL - ntok(header + "\n\n"))
        pieces = [q for p in _split_text(sec["text"], budget) for q in _fit(header, p, budget)]
        for piece in pieces:
            if ntok(piece) < MIN_TOKENS and len(sections) > 1:
                continue
            idx = len(chunks)
            text = f"{header}\n\n{piece}"
            chunks.append({
                "chunk_id": hashlib.md5(f"{meta['url']}#{idx}".encode()).hexdigest()[:12],
                "doc_url": meta["url"],
                "provider": meta["provider"],
                "api": meta["api"],
                "kind": meta["kind"],
                "licence": meta["licence"],
                "title": title,
                "section": sec["section"],
                "fetched_at": meta["fetched_at"],
                "effective_date": meta.get("effective_date"),
                "text": text,
                "n_tokens": model_tokens(text),          # what the embedding model sees (header and special tokens included)
            })
    return chunks
