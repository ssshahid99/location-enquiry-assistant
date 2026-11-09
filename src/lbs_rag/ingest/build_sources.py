"""Build config/sources.yaml from the ground-truth workbook (source_link + citations doc_url)."""
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
GT = ROOT / "data" / "ground_truth"

# host -> (provider, licence)
PROVIDERS = {
    "maps.olakrutrim.com": ("Ola Maps", "vendor docs"),
    "developers.google.com": ("Google Maps Platform", "vendor docs"),
    "www.mapbox.com": ("Mapbox", "vendor docs"),
    "docs.mapbox.com": ("Mapbox", "vendor docs"),
    "docs.here.com": ("HERE", "vendor docs"),
    "project-osrm.org": ("OSRM", "BSD-2 / project docs"),
    "github.com": ("OSRM", "BSD-2 / project docs"),
    "operations.osmfoundation.org": ("OSM Nominatim", "OSMF policy"),
    "nominatim.org": ("OSM Nominatim", "GPL-2 / project docs"),
    "www.openstreetmap.org": ("OSM", "ODbL"),
    "stackoverflow.com": ("Stack Overflow", "CC BY-SA 4.0"),
    "www.rbi.org.in": ("RBI (FX rate)", "public reference"),
}
# URLs that need a real browser (JS-rendered)
JS_HOSTS = {"maps.olakrutrim.com", "docs.here.com", "www.mapbox.com", "www.rbi.org.in"}


def infer_api(url: str) -> str:
    u = url.lower()
    for key, api in [
        ("distance-matrix", "Distance Matrix"), ("matrix", "Distance Matrix"),
        ("autocomplete", "Autocomplete"), ("autosuggest", "Autocomplete"),
        ("search-box", "Autocomplete"), ("session-pricing", "Autocomplete"),
        ("geocod", "Reverse Geocode"), ("reverse", "Reverse Geocode"),
        ("directions", "Directions"), ("routes", "Directions"), ("routing", "Directions"),
        ("osrm", "Directions"),
    ]:
        if key in u:
            return api
    return "general"


def kind_of(url: str, host: str) -> str:
    if host == "stackoverflow.com":
        return "stackoverflow"
    if url.endswith((".yaml", ".yml")):
        return "openapi"
    return "html"


def main() -> None:
    review = pd.read_csv(GT / "gt.csv")
    cites = pd.read_csv(GT / "gt_citations.csv")
    urls = set(review["source_link"].dropna()) | set(cites["doc_url"].dropna())
    # gold-citation count per URL (retrieval priority + sanity)
    n_cite = cites.groupby("doc_url").size().to_dict()

    out = []
    for url in sorted(urls):
        host = urlparse(url).netloc
        provider, licence = PROVIDERS.get(host, ("unknown", "unknown"))
        kind = kind_of(url, host)
        out.append({
            "url": url,
            "provider": provider,
            "api": infer_api(url) if kind != "stackoverflow" else "mixed",
            "kind": kind,
            "licence": licence,
            "fetch_method": "stackexchange_api" if kind == "stackoverflow"
                            else ("httpx" if kind == "openapi"
                                  else ("playwright" if host in JS_HOSTS else "httpx")),
            "gold_citations": int(n_cite.get(url, 0)),
        })
    (ROOT / "config" / "sources.yaml").write_text(
        yaml.safe_dump({"sources": out}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    by_kind = pd.Series([o["kind"] for o in out]).value_counts().to_dict()
    print(f"wrote {len(out)} sources: {by_kind}")
    unknown = [o["url"] for o in out if o["provider"] == "unknown"]
    if unknown:
        print("UNKNOWN PROVIDER:", unknown)


if __name__ == "__main__":
    main()
