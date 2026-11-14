"""Verified limits injected into the prompt so a small model quotes them from config, not from memory."""
from ..retrieve.query_analyzer import detect
from .pricing_calc import load_pricing


def relevant_limits(query: str) -> list[str]:
    d = detect(query)
    # no provider named (e.g. "which APIs can optimise 20 stops, and what are the limits?"): give every provider's limits so
    # the model does not fill the gap from memory (it invented 23/100/25-waypoint limits in review)
    provs = set(d["providers"]) or {"Google Maps Platform", "Ola Maps", "Mapbox", "OSM Nominatim", "OSRM"}
    apis = set(d["apis"])
    L = load_pricing()["limits"]
    out = []
    dm = "Distance Matrix" in apis
    routing = "Directions" in apis or dm
    if "Google Maps Platform" in provs:
        if dm:
            g = L["google.distance_matrix"]
            out.append(f"Google Distance Matrix: max {g['max_origins']} origins, {g['max_destinations']} destinations and "
                       f"{g['max_elements']} elements per request.")
        if routing:
            r, w = L["google.routes_rate"], L["google.directions"]
            out.append(f"Google Routes: max {r['max_requests_per_minute']:,} requests per minute for Compute Routes and "
                       f"{r['max_matrix_elements_per_minute']:,} elements per minute for Compute Route Matrix; "
                       f"max {w['max_waypoints']} waypoints per request; more than "
                       f"{w['essentials_max_intermediate_waypoints']} intermediate waypoints bills as the Pro tier.")
    if "Ola Maps" in provs:
        if dm:
            out.append(f"Ola Distance Matrix: max {L['ola.distance_matrix']['max_pairs']} origin-destination pairs per request "
                       "(origins x destinations <= 50).")
        if "Directions" in apis:
            out.append(f"Ola Directions: up to {L['ola.directions']['max_waypoints']} waypoints; Directions Basic: up to "
                       f"{L['ola.directions_basic']['max_waypoints']} waypoints.")
    if "Mapbox" in provs:
        if dm:
            m = L["mapbox.matrix"]
            out.append(f"Mapbox Matrix: max {m['max_coordinates']} coordinates ({m['max_coordinates_driving_traffic']} for "
                       f"driving-traffic) and {m['max_elements']} elements per request.")
        if "Directions" in apis:
            out.append(f"Mapbox Directions: max {L['mapbox.directions']['max_coordinates']} coordinates per request.")
    if "OSM Nominatim" in provs:
        out.append("OSM Nominatim public server: absolute maximum of 1 request per second; no client-side type-ahead "
                   "autocomplete; heavy use needs self-hosting.")
    if "OSRM" in provs:
        out.append("OSRM public demo server: at most 1 request per second, non-commercial use only, no uptime guarantee.")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Auditable facts (config/facts.yaml): each fact has a verbatim quote from a fetched vendor page.
import re
from functools import lru_cache
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(s).lower())).strip()


@lru_cache(maxsize=1)
def _facts() -> list[dict]:
    raw = yaml.safe_load((_ROOT / "config" / "facts.yaml").read_text(encoding="utf-8"))["facts"]
    return [{**f, "_rx": [re.compile(p, re.I) for p in f["any"]]} for f in raw]


@lru_cache(maxsize=1)
def _chunks_by_doc() -> dict[str, list[tuple[str, dict]]]:
    from ..index.bm25 import load_chunks
    out: dict[str, list[tuple[str, dict]]] = {}
    for c in load_chunks():
        out.setdefault(c["doc_url"], []).append((norm(c["text"]), c))
    return out


def find_chunk(url: str, quote: str) -> dict | None:
    """The chunk of `url` whose text contains the quote (normalised), or None."""
    q = norm(quote)
    for text, c in _chunks_by_doc().get(url, []):
        if q in text:
            return c
    return None


def matching_facts(query: str) -> list[dict]:
    return [f for f in _facts() if any(rx.search(query) for rx in f["_rx"])]


def attach_facts(hits: list[dict], facts: list[dict]) -> list[str]:
    """Add each matched fact's source chunk(s) to `hits` (so the model can cite them) and return prompt lines
    'fact text [n] [m]'. A fact whose quote cannot be found in the corpus is skipped, never injected unsourced."""
    from ..retrieve.hybrid import _hit
    lines = []
    for f in facts:
        idxs = []
        for s in f["sources"]:
            c = find_chunk(s["url"], s["quote"])
            if c is None:
                continue
            if not any(h["chunk_id"] == c["chunk_id"] for h in hits):
                hits.append(_hit(c, 1.0))
            idxs.append(next(i for i, h in enumerate(hits, 1) if h["chunk_id"] == c["chunk_id"]))
        if idxs:
            lines.append(f"{f['text']} " + " ".join(f"[{i}]" for i in dict.fromkeys(idxs)))
    return lines