"""Section -> token-bounded chunks with metadata. Every chunk carries provenance for citations."""
import hashlib
import re

import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")
import os

MAX_TOKENS = int(os.environ.get("LBS_CHUNK_TOKENS", "450"))        # experiment knob; 450 is the current index
OVERLAP = int(os.environ.get("LBS_CHUNK_OVERLAP", "60"))
MIN_TOKENS = 6      # keep short callouts ("As of October 2026 ... no longer ...") - they are often the answer


def ntok(s: str) -> int:
    return len(_ENC.encode(s))


def _split_text(text: str) -> list[str]:
    """Pack paragraphs up to MAX_TOKENS; hard-split oversized paragraphs by tokens with overlap."""
    parts = []
    for p in re.split(r"\n\s*\n", text):
        p = p.strip()
        if not p:
            continue
        # oversized block (OpenAPI params, tables): split on lines so rows/params stay whole
        parts += [l for l in p.split("\n") if l.strip()] if ntok(p) > MAX_TOKENS else [p]
    out, cur, cur_n = [], [], 0
    for p in parts:
        n = ntok(p)
        if n > MAX_TOKENS:
            if cur:
                out.append("\n\n".join(cur)); cur, cur_n = [], 0
            ids = _ENC.encode(p)
            step = MAX_TOKENS - OVERLAP
            for i in range(0, len(ids), step):
                out.append(_ENC.decode(ids[i:i + MAX_TOKENS]))
                if i + MAX_TOKENS >= len(ids):
                    break
            continue
        if cur_n + n > MAX_TOKENS and cur:
            out.append("\n\n".join(cur))
            tail = cur[-1] if ntok(cur[-1]) <= OVERLAP else ""
            cur, cur_n = ([tail] if tail else []), (ntok(tail) if tail else 0)
        cur.append(p)
        cur_n += n
    if cur:
        out.append("\n\n".join(cur))
    return out


def chunk_document(meta: dict, title: str, sections: list[dict]) -> list[dict]:
    """meta: url, provider, api, kind, licence, fetched_at, effective_date, ..."""
    chunks = []
    for sec in sections:
        for piece in _split_text(sec["text"]):
            if ntok(piece) < MIN_TOKENS and len(sections) > 1:
                continue
            idx = len(chunks)
            header = f"{meta['provider']} | {title} > {sec['section']}" if sec["section"] != title else f"{meta['provider']} | {title}"
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
                "text": f"{header}\n\n{piece}",
                "n_tokens": ntok(piece),
            })
    return chunks
