"""Parse + chunk the latest raw snapshot -> data/processed/chunks.jsonl."""
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

from .chunk import chunk_document
from .fetch import slug
from .parse_html import parse_html
from .parse_openapi import parse_openapi
from .parse_stackoverflow import parse_stackoverflow

ROOT = Path(__file__).resolve().parents[3]


def parse_source(s: dict, text: str):
    """Parse one fetched source -> (title, sections, effective_date). Shared by the full ingest and the freshness tooling."""
    if s["kind"] == "stackoverflow":
        title, sections, extra = parse_stackoverflow(json.loads(text))
        return title, sections, extra["effective_date"]
    if s["kind"] == "openapi":
        title, sections = parse_openapi(text)
        return title, sections, None
    title, sections = parse_html(text)
    return title, sections, None


def main(day: str | None = None) -> None:
    raw_root = ROOT / "data" / "raw"
    day = day or sorted(p.name for p in raw_root.iterdir() if p.is_dir())[-1]
    rawdir = raw_root / day
    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    fetched = {}
    for line in (rawdir / "manifest.jsonl").read_text(encoding="utf-8-sig").splitlines():
        m = json.loads(line)
        if m["status"] == "ok":
            fetched[m["url"]] = m["fetched_at"]

    all_chunks, per_doc, problems = [], {}, []
    for s in sources:
        url = s["url"]
        ext = {"stackoverflow": "json", "openapi": "yaml"}.get(s["kind"], "html")
        path = rawdir / f"{slug(url)}.{ext}"
        if not path.exists():
            problems.append((url, "missing snapshot")); continue
        meta = {**s, "fetched_at": fetched.get(url, day), "effective_date": None}
        try:
            title, sections, meta["effective_date"] = parse_source(s, path.read_text(encoding="utf-8"))
            ch = chunk_document(meta, title or url, sections)
        except Exception as e:  # noqa: BLE001
            problems.append((url, f"{type(e).__name__}: {e}")); continue
        if not ch:
            problems.append((url, "0 chunks")); continue
        per_doc[url] = len(ch)
        all_chunks += ch

    out = ROOT / "data" / "processed"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    by_kind = Counter(c["kind"] for c in all_chunks)
    toks = [c["n_tokens"] for c in all_chunks]
    print(f"{len(all_chunks)} chunks from {len(per_doc)}/{len(sources)} docs  {dict(by_kind)}")
    print(f"tokens/chunk: min {min(toks)} median {sorted(toks)[len(toks)//2]} max {max(toks)}")
    for u, why in problems:
        print("PROBLEM", u, why)
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
