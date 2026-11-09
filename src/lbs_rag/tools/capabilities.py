"""Capability matcher: customer requirement -> documented capability per provider, with citations (project problem 8).

Evidence comes only from config/capabilities.yaml, where every entry has a verbatim quote verified against the fetched pages.
A provider without evidence is returned as a GAP ("not documented in the fetched sources"): something to confirm with the vendor,
never a claim that the capability is absent.
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

from ..retrieve.query_analyzer import detect
from .facts import find_chunk

ROOT = Path(__file__).resolve().parents[3]
PRICED_AND_FREE = ["Ola Maps", "Google Maps Platform", "Mapbox", "HERE", "OSM Nominatim", "OSRM"]


@lru_cache(maxsize=1)
def library() -> list[dict]:
    raw = yaml.safe_load((ROOT / "config" / "capabilities.yaml").read_text(encoding="utf-8"))["capabilities"]
    return [{**c, "_rx": [re.compile(p, re.I) for p in c["match"]]} for c in raw]


def relevant_capabilities(requirement: str) -> list[dict]:
    return [c for c in library() if any(rx.search(requirement) for rx in c["_rx"])]


def match_capabilities(requirement: str, providers: list[str] | None = None) -> list[dict]:
    """-> [{id, label, providers: [{provider, documented, statement, url, quote, chunk_id}], gaps: [provider, ...]}]
    `providers` defaults to the providers named in the requirement, else all six."""
    named = providers or detect(requirement)["providers"] or PRICED_AND_FREE
    out = []
    for cap in relevant_capabilities(requirement):
        rows, gaps = [], []
        for prov in named:
            ev = cap["evidence"].get(prov)
            if not ev:
                gaps.append(prov)
                rows.append({"provider": prov, "documented": False,
                             "statement": "Not documented in the fetched sources; confirm with the vendor.", "url": None, "quote": None,
                             "chunk_id": None})
                continue
            e = ev[0]
            chunk = find_chunk(e["url"], e["quote"])          # the cited passage must exist in our corpus
            rows.append({"provider": prov, "documented": chunk is not None, "statement": e["statement"], "url": e["url"],
                         "quote": e["quote"], "chunk_id": chunk["chunk_id"] if chunk else None})
            if chunk is None:
                gaps.append(prov)
        out.append({"id": cap["id"], "label": cap["label"], "providers": rows, "gaps": gaps})
    return out


def render_table(matches: list[dict]) -> str:
    """Plain-text comparison used inside drafts. Documented rows show their source URL; gaps are labelled as gaps."""
    lines = []
    for m in matches:
        lines.append(f"{m['label']}:")
        for r in m["providers"]:
            tag = "" if r["documented"] else " [GAP]"
            src = f" ({r['url']})" if r["url"] else ""
            lines.append(f"- {r['provider']}{tag}: {r['statement']}{src}")
    return "\n".join(lines)
