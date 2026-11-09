"""Documentation freshness (checklist R7): what changed since the last snapshot, and re-index only that.

  python -m lbs_rag.ingest.fetch                                  fetch a new dated snapshot into data/raw/<date>/ (existing step)
  python -m lbs_rag.ingest.freshness age                          how old is the indexed documentation (and is it past the warning age)
  python -m lbs_rag.ingest.freshness compare [--old D] [--new D]  per page: changed / added / removed / unchanged / unreadable
  python -m lbs_rag.ingest.freshness reindex [--new D] [--apply]  re-chunk and re-embed ONLY the changed pages (dry run unless --apply)

"Changed" compares the PARSED text (titles, headings, content) of each page, not the raw HTML, so ads, nonces and timestamps in the page
source do not count as changes. Stack Overflow pages are compared on question and answer text, not on scores or view counts.
Nothing here edits config/facts.yaml, capabilities.yaml or pricing.yaml: after a refresh, run the tests (they verify every verbatim quote
against the new text) and the price sweep; a failing quote is a fact that changed and needs a person to update it.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .chunk import chunk_document
from .fetch import slug
from .run_ingest import parse_source

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
CHUNKS = ROOT / "data" / "processed" / "chunks.jsonl"


# ------------------------------------------------------------------------------------------------ helpers
def snapshots(raw_root: Path = RAW) -> list[str]:
    return sorted(p.name for p in raw_root.iterdir() if p.is_dir()) if raw_root.exists() else []


def _sources() -> list[dict]:
    return yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]


def _ext(s: dict) -> str:
    return {"stackoverflow": "json", "openapi": "yaml"}.get(s["kind"], "html")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def fingerprint(s: dict, path: Path) -> str | None:
    """Hash of the parsed content, or None when the page cannot be read or parsed."""
    try:
        title, sections, _ = parse_source(s, path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    body = " ".join(f"{sec.get('heading', '')} {sec.get('text', '')}" for sec in sections)
    return hashlib.sha256(_norm(f"{title or ''} {body}").encode("utf-8")).hexdigest()[:16]


def compare(old_day: str, new_day: str, *, raw_root: Path = RAW, sources: list[dict] | None = None) -> dict:
    """{changed, added, removed, unchanged, unreadable: [url, ...]}.
    added = fetched in the new snapshot only; removed = fetched in the old one only; unreadable = present but could not be parsed."""
    out: dict[str, list[str]] = {"changed": [], "added": [], "removed": [], "unchanged": [], "unreadable": []}
    for s in sources or _sources():
        name = f"{slug(s['url'])}.{_ext(s)}"
        po, pn = raw_root / old_day / name, raw_root / new_day / name
        if not po.exists() and not pn.exists():
            continue
        if not po.exists():
            out["added"].append(s["url"])
        elif not pn.exists():
            out["removed"].append(s["url"])
        else:
            fo, fn = fingerprint(s, po), fingerprint(s, pn)
            out["unreadable" if fo is None or fn is None else "changed" if fo != fn else "unchanged"].append(s["url"])
    return out


# ------------------------------------------------------------------------------------------------ age
def snapshot_info(chunks_path: Path = CHUNKS, now: datetime | None = None) -> dict:
    """The fetch dates inside the INDEX itself (what answers are really based on): oldest, newest, age in days, warning flag."""
    dates = set()
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line).get("fetched_at")
            if d:
                dates.add(d[:10])
    if not dates:
        return {"oldest": None, "newest": None, "age_days": None, "stale": None}
    now = now or datetime.now(timezone.utc)
    oldest, newest = min(dates), max(dates)
    age = (now.date() - datetime.fromisoformat(oldest).date()).days
    limit = (yaml.safe_load((ROOT / "config" / "freshness.yaml").read_text(encoding="utf-8")) or {}).get("max_age_days", 45)
    return {"oldest": oldest, "newest": newest, "age_days": age, "max_age_days": limit, "stale": age > limit}


# ------------------------------------------------------------------------------------------------ incremental re-index
def replace_doc_chunks(old: list[dict], new_by_url: dict[str, list[dict]], order: list[str]) -> list[dict]:
    """Pure: the chunk list after replacing ALL chunks of the given pages, keeping the sources.yaml page order. Pages not listed are untouched."""
    keep: dict[str, list[dict]] = {}
    for c in old:
        if c["doc_url"] not in new_by_url:
            keep.setdefault(c["doc_url"], []).append(c)
    merged = {**keep, **new_by_url}
    ordered = [u for u in order if u in merged] + [u for u in merged if u not in order]
    return [c for u in ordered for c in merged[u]]


def rebuild_chunks(day: str, urls: list[str], raw_root: Path = RAW) -> dict[str, list[dict]]:
    manifest = {}
    for line in (raw_root / day / "manifest.jsonl").read_text(encoding="utf-8-sig").splitlines():
        m = json.loads(line)
        if m["status"] == "ok":
            manifest[m["url"]] = m["fetched_at"]
    by_url = {}
    for s in _sources():
        if s["url"] not in urls:
            continue
        meta = {**s, "fetched_at": manifest.get(s["url"], day), "effective_date": None}
        title, sections, meta["effective_date"] = parse_source(s, (raw_root / day / f"{slug(s['url'])}.{_ext(s)}").read_text(encoding="utf-8"))
        by_url[s["url"]] = chunk_document(meta, title or s["url"], sections)
    return by_url


def reindex(day: str, urls: list[str], *, apply: bool = False) -> dict:
    """Re-chunk and re-embed only `urls`. Dry run by default. With apply: back up chunks.jsonl, replace those pages' chunks, replace their
    vectors in Chroma (delete by page, add new). Returns counts."""
    new = rebuild_chunks(day, urls)
    old = [json.loads(line) for line in CHUNKS.read_text(encoding="utf-8").splitlines() if line.strip()]
    before = sum(1 for c in old if c["doc_url"] in new)
    merged = replace_doc_chunks(old, new, [s["url"] for s in _sources()])
    res = {"pages": len(new), "chunks_before": before, "chunks_after": sum(len(v) for v in new.values()), "total_after": len(merged), "applied": apply}
    if not apply:
        return res
    from ..index.embed import embed_docs
    from ..index.vector_store import add_chunks, get_collection
    backup = CHUNKS.with_name(f"chunks.jsonl.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(CHUNKS, backup)
    CHUNKS.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in merged) + "\n", encoding="utf-8")
    fresh = [c for v in new.values() for c in v]
    col = get_collection()
    for u in new:
        col.delete(where={"doc_url": u})
    if fresh:
        add_chunks(col, fresh, embed_docs([c["text"] for c in fresh]))
    res["backup"] = str(backup)
    res["vectors_in_store"] = col.count()
    return res


# ------------------------------------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="freshness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("age")
    for name in ("compare", "reindex"):
        p = sub.add_parser(name)
        p.add_argument("--old")
        p.add_argument("--new")
        if name == "reindex":
            p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "age":
        i = snapshot_info()
        print(f"indexed pages fetched {i['oldest']} .. {i['newest']}; oldest is {i['age_days']} days old (warning after {i.get('max_age_days')})"
              + ("  ** STALE: fetch a new snapshot **" if i["stale"] else ""))
        return 1 if i["stale"] else 0
    days = snapshots()
    new_day = a.new or (days[-1] if days else None)
    old_day = getattr(a, "old", None) or (days[-2] if len(days) >= 2 else None)
    if not new_day or not old_day:
        print(f"need two snapshots in data/raw (have {days}); run `python -m lbs_rag.ingest.fetch` to take a new one")
        return 2
    diff = compare(old_day, new_day)
    print(f"compare {old_day} -> {new_day}: " + ", ".join(f"{k} {len(v)}" for k, v in diff.items()))
    for k in ("changed", "added", "removed", "unreadable"):
        for u in diff[k]:
            print(f"  {k:<10} {u}")
    if a.cmd == "reindex":
        urls = diff["changed"] + diff["added"]
        if not urls:
            print("nothing to re-index")
            return 0
        print(reindex(new_day, urls, apply=a.apply))
        if not a.apply:
            print("dry run: add --apply to re-index. Afterwards run the tests and eval/pricing_sweep.py; a failing verbatim quote is a changed fact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
