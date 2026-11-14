"""Rate card: list prices for "compare the Directions API price between Ola Maps and Google Maps" (a price question with NO volume).

The calculator needs a volume, so such questions used to fall through to the model, which answered from whatever pages retrieval happened
to return (one provider's price page, the other missing). Here code writes the whole answer from config/pricing.yaml: for every named
provider and API, the free allowance and the marginal tier rates, plus the honest statement for HERE / open-source providers.
No model call, no invented numbers. If the customer gives a volume, the normal calculator path is used instead.
"""
import re
from decimal import Decimal

from ..retrieve.query_analyzer import detect
from .pricing_calc import load_pricing, usd_to_inr

_PRICE_Q = re.compile(r"\b(price|prices|pricing|cost|costs|how much|cheap(?:er|est)|rates?(?![ -]?limit)|per 1,?000|charges?|expensive|compare|comparison)\b", re.I)
_EXPLAIN = re.compile(r"\b(why|how (?:do|can|to|should)|error|fails?|invoice|bill(?:ed|ing)?|charged|overcharg\w*|wrong|dispute|supports?|supported|"
                      r"limits?|quota|throttl\w*|session[- ]based)\b", re.I)
_API_RX = {"Directions": r"\bdirections?\b|\brout(?:e|es|ing)\b", "Distance Matrix": r"\bmatri(?:x|ces)\b", "Reverse Geocode": r"\bgeocod\w*",
           "Autocomplete": r"\bauto-?complete\b|\bplace details\b|\bsearch box\b"}
_PRICED = ("Ola Maps", "Google Maps Platform", "Mapbox")
_TAG = {"essentials": "Essentials (no traffic)", "pro": "Pro (traffic-aware or modifiers)", "directions_basic": "Basic (no traffic)",
        "distance_matrix_basic": "Basic (no traffic)", "ola.directions": "Standard (traffic-aware)", "ola.distance_matrix": "Standard (traffic-aware)"}


def apis_in(query: str) -> list[str]:
    return [a for a, rx in _API_RX.items() if re.search(rx, query, re.I)]


def _tag(sid: str) -> str:
    for k in ("essentials", "pro"):
        if sid.endswith("_" + k):
            return _TAG[k]
    return _TAG.get(sid) or _TAG.get(sid.split(".", 1)[1]) or sid.split(".", 1)[1].replace("_", " ").title()


def _plain(x: Decimal) -> str:
    return f"{x.normalize():f}"


def _money(cur: str, x: Decimal | float, per: int, unit: str) -> str:
    x = Decimal(str(x))
    if cur == "INR":
        return (f"INR {_plain(x)} per {unit}" + (f" (INR {_plain(x * 1000)} per 1,000)" if per == 1 else "")) if per == 1 \
            else f"INR {_plain(x)} per {per:,} {unit}s"
    return f"USD {x:.2f} per {per:,} {unit}s (about INR {usd_to_inr(x):,.2f})"


def _tiers(sku: dict) -> str:
    cur, per, unit = sku["currency"], sku["per"], sku["unit"]
    parts, lo = [], 0
    for bound, rate in sku["tiers"]:
        if rate == 0:
            parts.append(f"first {bound:,} {unit}s a month free")
        elif bound is None:
            parts.append(f"then {_money(cur, rate, per, unit)}")
        else:
            parts.append(f"{lo + 1:,} to {bound:,}: {_money(cur, rate, per, unit)}")
        lo = bound or lo
    if sku.get("beyond") == "contact_sales":
        parts.append(f"above {lo:,}: no published list price (contact sales)")
    return "; ".join(parts)


def _skus(provider: str, api: str) -> list[tuple[str, dict]]:
    out = []
    for sid, sku in load_pricing()["skus"].items():
        if sku["provider"] == provider and sku["api"] == api and "legacy" not in sid and not sid.endswith("_enterprise") \
                and "optimization" not in sid:
            out.append((sid, sku))
    return out


def is_rate_card_question(query: str) -> bool:
    from ..agent.planner import mentions_pricing  # noqa: F401  (same notion of "price question" as the router)
    from .pricing_intent import has_volume
    if not _PRICE_Q.search(query) or _EXPLAIN.search(query) or has_volume(query):
        return False
    if not apis_in(query):
        return False
    provs = detect(query)["providers"]
    return bool(provs) or bool(re.search(r"\b(which|each|every|all|cheapest|compare|comparison)\b", query, re.I))


def answer_text(query: str) -> tuple[str, list[str]] | None:
    """-> (text, priced providers whose list-price page should be cited) or None if this is not a volume-free price question."""
    if not is_rate_card_question(query):
        return None
    apis = apis_in(query)
    named = detect(query)["providers"]
    provs = [p for p in _PRICED if p in named] if named else list(_PRICED)
    lines, cited = [], []
    for api in apis:
        block = []
        for prov in provs:
            rows = _skus(prov, api)
            if not rows:
                block.append(f"- {prov}: no verified {api} price in the price list I hold.")
                continue
            cited.append(prov)
            block.append(f"- {prov}:")
            block += [f"  - {_tag(sid)}: {_tiers(sku)}." for sid, sku in rows]
        if "HERE" in named:
            block.append("- HERE: the list price could not be verified (its price page did not render), so I won't state one; ask HERE for a quote.")
        if "Mappls (MapmyIndia)" in named:
            block.append("- Mappls (MapmyIndia): no list price is published on its official pages, so I won't state one; ask Mappls for a quote.")
        for oss in ("OSM Nominatim", "OSRM"):
            if oss in named:
                block.append(f"- {oss}: free open-source software with no per-request price; the public server has usage limits (about 1 request "
                             "per second) and heavy use needs self-hosting.")
        lines.append(f"{api} list prices:\n" + "\n".join(block))
    if not cited:
        return None
    fx = load_pricing()["fx"]
    foot = ("Tiers are marginal: each rate applies only to the requests inside its band. Google prices are the India price list in USD "
            f"(converted at {fx['usd_inr']} INR per USD, {fx['source']}, {fx['as_of']}). "
            "Tell me your monthly volume and I will work out the exact total for each provider.")
    return "\n\n".join(lines) + "\n\n" + foot, list(dict.fromkeys(cited))
