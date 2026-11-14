"""Abstention: decide when the system must not state a fact it cannot support."""
import re

ABSTAIN_PHRASES = re.compile(
    r"(not (?:contain|include|provide|specif|mention|covered)|no (?:verified|published|information|data)|cannot (?:give|provide|confirm|verify)|"
    r"could not (?:be )?verif|don't have|do not have|unable to (?:find|confirm|verify)|ask (?:the vendor|HERE|Ola)|contact sales)", re.I)


# Providers a user may ask about that are NOT in the knowledge base. The system must say so, not improvise.
OUT_OF_SCOPE = {
    "TomTom": r"tomtom", "Azure Maps": r"azure maps|bing maps", "Esri / ArcGIS": r"esri|arcgis",
    "Apple Maps": r"apple maps|mapkit",
    "Geoapify": r"geoapify", "LocationIQ": r"locationiq", "MapTiler": r"maptiler", "Radar": r"\bradar\b",
    "Amazon Location": r"amazon location|aws location", "Waze": r"\bwaze\b", "Baidu / Gaode": r"baidu|amap\b|gaode",
}
_KNOWN = r"google|mapbox|\bhere\b|\bola\b|krutrim|osrm|nominatim|openstreetmap|\bosm\b|mappls|mapmyindia|map my india"


def unsupported_providers(query: str) -> tuple[list[str], bool]:
    """-> (out-of-scope providers named in the query, whether any in-scope provider is also named)."""
    q = query.lower()
    out = [n for n, pat in OUT_OF_SCOPE.items() if re.search(pat, q)]
    return out, bool(re.search(_KNOWN, q))


def pricing_blocked(pricing: dict | None) -> bool:
    """True when the user asked for prices and none could be produced (e.g. HERE-only)."""
    return bool(pricing and pricing.get("pricing") and not pricing["results"]
                and (pricing["unverified"] or pricing["errors"]))


def looks_abstained(answer_text: str) -> bool:
    """Heuristic over the model's own text; used for the eval's abstention score."""
    return bool(ABSTAIN_PHRASES.search(answer_text))
