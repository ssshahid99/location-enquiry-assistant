"""Free-tier questions answered by code from config/pricing.yaml (no model prose).

Two shapes:
  (a) "Which APIs have a free monthly allowance?"            -> allowance table per provider
  (b) "300 requests a day, 2 origins x 3 destinations: do we stay inside the free cap?" -> workload arithmetic
"""
import re
from decimal import Decimal

from ..retrieve.query_analyzer import detect
from .pricing_calc import load_pricing, quote

DAYS = 30                       # the reference answers convert per-day to per-month with 30 days
_FREE_Q = re.compile(r"free (monthly |usage |tier |allowance |cap |calls )|\bfree\b.{0,40}\b(tier|cap|allowance)\b|inside the free|"
                     r"(stay|within|under|inside).{0,30}(free|cap)|buy credits|need.{0,20}credits", re.I)
_BILLING = re.compile(r"\b(invoice|bill|billing|charged?|overcharg)\w*\b.{0,60}\b(count|wrong|higher|doubled|jump|seems)", re.I)
_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(M|K)?"
_API_KEYS = {"Directions": "directions", "Distance Matrix": "matrix", "Autocomplete": "autocomplete", "Reverse Geocode": "geocod"}


def is_free_tier_question(q: str) -> bool:
    return bool(_FREE_Q.search(q)) and not _BILLING.search(q)


def _n(s: str, suf: str | None) -> int:
    return int(float(s.replace(",", "")) * {"M": 1_000_000, "K": 1_000}.get((suf or "").upper(), 1))


def _free_cap(sku: dict) -> int | None:
    t = sku["tiers"][0]
    return t[0] if t[1] == 0 else None


def _skus_for(provider: str, apis: set[str], want_enterprise: bool | None = None) -> list[tuple[str, dict, int]]:
    out = []
    for sid, sku in load_pricing()["skus"].items():
        if sku["provider"] != provider or "legacy" in sid:
            continue
        if apis and sku["api"] not in apis:
            continue
        if want_enterprise is False and sid.endswith("_enterprise"):
            continue
        cap = _free_cap(sku)
        if cap is not None:
            out.append((sid, sku, cap))
    return out


def _label(sid: str) -> str:
    return sid.split(".", 1)[1].replace("_", " ").title()


def allowance_text(apis: set[str]) -> str:
    """(a) free allowance per provider for the detected API(s)."""
    lines = []
    for prov, note in (("Ola Maps", "first {cap} per month, per API"), ("Google Maps Platform", "India list, free cap per month"),
                       ("Mapbox", "per month")):
        rows = _skus_for(prov, apis)
        if not rows:
            continue
        if prov == "Ola Maps":
            caps = {c for _, _, c in rows}
            lab = ", ".join(_label(s) for s, _, _ in rows)
            extra = ""
            if "Distance Matrix" in (apis or {"Distance Matrix"}) and any("distance" in s for s, _, _ in rows):
                extra = (" Distance Matrix is billed per pair; whether the free calls count pairs or requests is unconfirmed "
                         f"(config assumes {load_pricing()['ola']['distance_matrix_free_counts']}).")
            lines.append(f"- Ola Maps: {', '.join(f'{c:,}' for c in sorted(caps))} calls free each month per API ({lab}).{extra}")
        else:
            lines.append(f"- {prov} ({note.replace('{cap}', '')}): " + "; ".join(f"{_label(s)} {c:,} {sku['unit']}s" for s, sku, c in rows) + ".")
    lines.append("- HERE: the free allowance could not be verified (its pricing page did not render); check HERE's plan page.")
    lines.append("- OSRM / OSM Nominatim: open-source software; the public servers are for light, non-commercial use "
                 "(about 1 request per second), so they are not a production free tier.")
    return "Free monthly allowances (from the verified price lists):\n" + "\n".join(lines)


def _parse_volume(q: str) -> tuple[int | None, str]:
    """-> (units per day or month, 'day'|'month')."""
    d = re.search(_NUM + r"[^\d]{0,50}?\b(?:requests?|calls?)\b[^\d.]{0,12}\ba day\b|" + _NUM + r"[^\d]{0,40}\bper day\b", q, re.I)
    if d:
        g = [x for x in d.groups() if x is not None]
        return _n(g[0], g[1] if len(g) > 1 else None), "day"
    m = re.search(_NUM + r"[^\d]{0,50}?\b(?:requests?|calls?|elements?|pairs|lookups)\b[^\d]{0,25}(?:a|per) month|about " + _NUM + r"\s+requests", q, re.I)
    if m:
        g = [x for x in m.groups() if x is not None]
        return _n(g[0], g[1] if len(g) > 1 else None), "month"
    return None, "month"


def _parse_dims(q: str) -> tuple[int, int] | None:
    m = re.search(r"(\d+)\s*origins?\s*(?:and|[×x,])\s*(\d+)\s*destinations?", q, re.I) or re.search(r"(\d+)\s*[×x]\s*(\d+)", q)
    return (int(m.group(1)), int(m.group(2))) if m else None


def workload_text(q: str, providers: list[str], apis: set[str]) -> str | None:
    """(b) arithmetic for one stated workload; returns None if no volume can be read."""
    vol, per = _parse_volume(q)
    if vol is None:
        return None
    dims = _parse_dims(q) if "Distance Matrix" in apis else None
    per_req = dims[0] * dims[1] if dims else 1
    monthly_requests = vol * (DAYS if per == "day" else 1)
    units = monthly_requests * per_req
    how = f"{vol:,} requests a {per}" + (f" x {dims[0]} origins x {dims[1]} destinations = {per_req} elements per request" if dims else "")
    how += f" x {DAYS} days" if per == "day" else ""
    lines = [f"Your usage: {how} = {units:,} {'elements' if dims else 'requests'} a month."]
    for prov in providers or ["Google Maps Platform"]:
        rows = _skus_for(prov, apis, want_enterprise=bool(re.search(r"two[- ]?wheel|enterprise", q, re.I)))
        if not rows:
            continue
        for sid, sku, cap in rows:
            if prov == "Ola Maps" and dims and load_pricing()["ola"]["distance_matrix_free_counts"] == "requests":
                cmp_units, what = monthly_requests, "requests (Ola's free calls counted as requests; UNCONFIRMED)"
            else:
                cmp_units, what = units, sku["unit"] + "s"
            inside = cmp_units <= cap
            line = f"- {prov} {_label(sid)}: free cap {cap:,} {what}; {cmp_units:,} -> {'inside' if inside else 'OUTSIDE'} the cap"
            if not inside:
                qt = quote(sid, units)
                over = cmp_units - cap
                line += f" by {over:,}; list-price cost for the month: " + (
                    f"INR {qt.cost_inr:,.2f}" if sku["currency"] == "INR" else f"USD {qt.cost:,.2f} (about INR {qt.cost_inr:,.2f})")
            lines.append(line)
        if prov == "Google Maps Platform" and any("essentials" in s for s, _, _ in rows) and any(s.endswith("_pro") for s, _, _ in rows):
            lines.append("  Requests that use traffic (departure_time) or location modifiers bill as Advanced/Pro, which has the lower "
                         "free cap; compare against that line if you use them.")
        if prov == "Ola Maps":
            lines.append("  Within the free allowance no credits are needed; since 1 September 2026 usage beyond it draws on prepaid Ola Maps credits.")
    return "\n".join(lines)


def answer_text(query: str) -> tuple[str, list[str]] | None:
    """-> (text, providers whose price page should be cited) or None if this is not a free-tier question."""
    if not is_free_tier_question(query):
        return None
    d = detect(query)
    provs, apis = d["providers"], set(d["apis"])
    wl = workload_text(query, provs[:1] if len(provs) == 1 else provs, apis) if provs else None
    if wl:
        return wl, [p for p in provs if p in ("Ola Maps", "Google Maps Platform", "Mapbox")]
    if re.search(r"\bwhich\b|\bwhat\b.{0,40}\b(apis?|providers?)\b|\bdo(es)? .{0,20}(give|offer|have)\b", query, re.I) or not provs:
        return allowance_text(apis), ["Ola Maps", "Google Maps Platform", "Mapbox"]
    return None
