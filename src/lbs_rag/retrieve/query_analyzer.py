"""Query analysis: rule-based provider/api detection + optional LLM rewrite (Qwen3-8B)."""
import json
import re

from config.settings import openai_client, settings

PROVIDER_KEYWORDS = {
    "Google Maps Platform": ["google"],
    "Mapbox": ["mapbox"],
    "HERE": [r"\bhere\b", "here maps", "here platform"],
    "Ola Maps": [r"\bola\b", "krutrim"],
    "OSRM": ["osrm"],
    "OSM Nominatim": ["nominatim", "openstreetmap", r"\bosm\b"],
}
API_KEYWORDS = {
    "Distance Matrix": ["distance matrix", "matrix", "distancematrix"],
    "Autocomplete": ["autocomplete", "autosuggest", "search box"],
    "Reverse Geocode": ["reverse geocod", "geocod"],
    "Directions": ["directions", "routing", "route", "waypoint"],
}


def detect(query: str) -> dict:
    q = query.lower()
    providers = []
    for p, kws in PROVIDER_KEYWORDS.items():
        hit = any(re.search(k, q) for k in kws)
        if p == "HERE":
            # The lowercase word "here" is ordinary English ("as shown here"); the provider is written HERE (capitals) or "HERE Maps / Platform ...".
            hit = bool(re.search(r"\bHERE\b", query)) or bool(re.search(r"\bhere (?:maps|platform|routing|geocoding|technologies|api)\b", q))
        if hit:
            providers.append(p)
    apis = [a for a, kws in API_KEYWORDS.items() if any(re.search(k, q) for k in kws)]
    return {"providers": providers, "apis": apis}


_PROMPT = """You rewrite user questions into search queries for a documentation index about location/maps APIs \
(Google Maps Platform, Mapbox, HERE, Ola Maps, OSRM, Nominatim) covering pricing, limits, errors and usage.
Return ONLY JSON: {{"queries": ["...", "..."]}} with 1-3 short keyword-rich queries. Rules: keep every number, unit, \
API name and provider name exactly as written; expand abbreviations; if the question compares providers, give one query per \
provider; do not answer the question.
Question: {q}
/no_think"""


def _strip_think(s: str) -> str:
    return re.sub(r"<think>.*?</think>", "", s, flags=re.S).strip()


def rewrite(query: str) -> list[str]:
    """Return [original, *rewrites]. Falls back to [original] on any failure."""
    try:
        r = openai_client().chat.completions.create(
            model=settings.chat_model, temperature=0, max_tokens=250,
            messages=[{"role": "user", "content": _PROMPT.format(q=query)}])
        txt = _strip_think(r.choices[0].message.content or "")
        m = re.search(r"\{.*\}", txt, flags=re.S)
        qs = [x for x in json.loads(m.group(0))["queries"] if isinstance(x, str) and x.strip()][:3]
        return [query] + [x for x in qs if x.strip().lower() != query.strip().lower()]
    except Exception:  # noqa: BLE001 - rewrite is an optimisation, never a hard dependency
        return [query]
