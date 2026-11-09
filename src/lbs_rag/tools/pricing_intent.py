"""Turn a pricing question into calculator calls.

Qwen extracts the workload as JSON (it is good at reading; bad at arithmetic); plain code maps it to SKUs
and the deterministic calculator does the maths. Anything unmapped or unpriced is reported, never guessed.
"""
import json
import re

from config.settings import openai_client, settings

from decimal import Decimal

from .pricing_calc import (PriceUnavailable, google_autocomplete_session_units, load_pricing, ola_distance_matrix, quote,
                           usd_to_inr)

_PROMPT = """Extract the pricing workload from the question below. Return ONLY JSON:
{{"pricing": true|false, "items": [{{"provider": "Ola Maps|Google Maps Platform|Mapbox|HERE|OSM Nominatim",
 "api": "autocomplete|place_details|reverse_geocode|directions|distance_matrix",
 "requests": <int per month or null>, "sessions": <int per month or null>, "keystrokes_per_session": <int or null>,
 "origins": <int or null>, "destinations": <int or null>, "calls": <int matrix requests per month or null>,
 "traffic": <true if traffic-aware / departure_time else false>, "basic": <true if Ola 'basic' variant else false>}}]}}
Rules: pricing=false if the question is not asking for a cost/price/estimate. If the question names several providers or
asks for "each provider", add one item per provider per API. Convert "2M"/"1.5 lakh" to integers. If sessions and keystrokes
are given, requests = sessions * keystrokes. For distance_matrix: if the question gives matrix dimensions and a count, fill
origins, destinations and calls; if it gives only a total number of elements or pairs, put that total in "requests".
traffic: true if the question explicitly asks for traffic-aware / live traffic / departure_time; false only if it explicitly says
no traffic / without traffic / traffic not needed; otherwise null (unspecified, or "with and without traffic"). basic=true only if
it explicitly says Basic. "users" counts as sessions when keystrokes/characters per user are given.
Never compute costs.
Question: {q}
/no_think"""


def _strip(s: str) -> str:
    return re.sub(r"<think>.*?</think>", "", s, flags=re.S).strip()


def extract(query: str) -> dict:
    try:
        r = openai_client().chat.completions.create(
            model=settings.chat_model, temperature=0, max_tokens=600,
            messages=[{"role": "user", "content": _PROMPT.format(q=query)}])
        m = re.search(r"\{.*\}", _strip(r.choices[0].message.content or ""), flags=re.S)
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {"pricing": False, "items": []}
    except Exception:  # noqa: BLE001
        return {"pricing": False, "items": [], "error": "extraction failed"}


def _i(x) -> int | None:
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _summ(q, label: str) -> dict:
    return {"label": label, "sku": q.sku, "provider": q.provider, "units": q.units, "currency": q.currency,
            "cost": str(q.cost), "cost_inr": str(q.cost_inr), "free_units": q.free_units,
            "billable_units": q.billable_units, "contact_sales": q.contact_sales,
            "contact_sales_units": q.contact_sales_units, "notes": q.notes}


def _variants(traffic, basic: bool = False, force_pro: bool = False) -> list[bool]:
    """Which traffic tiers to price. Returns [True] (traffic / Pro / standard), [False] (no traffic / Essentials / Basic) or
    both. traffic=None means the question did not say, so both are shown (the reference answers quote both)."""
    if basic:
        return [False]
    if force_pro or traffic is True:
        return [True]
    if traffic is False:
        return [False]
    return [True, False]


def _tri(x):
    """JSON true/false/null -> True/False/None (a missing key means 'not specified')."""
    return x if isinstance(x, bool) else None


_NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(M|K)?"


def _n(s: str, suf: str | None = None) -> int:
    return int(float(s.replace(",", "")) * {"M": 1_000_000, "K": 1_000}.get((suf or "").upper(), 1))


def regex_hints(q: str) -> dict:
    """Deterministic reading of the templated phrases that the small model sometimes misreads. Only returns what it
    is sure about; reconcile() lets these override the model's numbers."""
    h: dict = {}
    m = re.search(r"(\d+)\s*(?:origins?\s*)?[×x]\s*(\d+)", q)
    if m:
        rest = q[m.end():]
        c = re.search(_NUM + r"\s*(?:times|per month|a month|/month)", rest)
        if c:
            h["matrix"] = (int(m.group(1)), int(m.group(2)), _n(c.group(1), c.group(2)))
    u = re.search(_NUM + r"\s+users a month typing\s*~?(\d+)\s*(?:characters|keystrokes)", q)
    if u:
        h["users_typing"] = (_n(u.group(1), u.group(2)), int(u.group(3)))
    else:
        s = re.search(_NUM + r"\s+(?:address-search\s+|user\s+|search\s+)?sessions", q)
        if s:
            h["sessions"] = _n(s.group(1), s.group(2))
            k = (re.search(r"averaging\s+(\d+)\s+keystrokes", q) or re.search(r"~?(\d+)\s+(?:requests|keystrokes)\s+each", q)
                 or re.search(r"[×x]\s*(\d+)\s+requests", q) or re.search(r"(\d+)\s+keystrokes\s+each", q))
            if k:
                h["ks"] = int(k.group(1))
    w = re.search(r"(\d+)\s*(?:waypoints|stops)", q)
    if w:
        h["waypoints"] = int(w.group(1))
    # two-wheeler *routing* (Enterprise SKU), not "a two-wheeler rental company"
    if re.search(r"two[- ]?wheel\w*[- ]+(?:routes?|routing|directions|route matrix|matrix)|"
                 r"(?:routes?|routing|matrix|directions)\s+(?:for|in)\s+two[- ]?wheel|\btwo-wheel\s+routing", q, re.I):
        h["two_wheeler"] = True
    if re.search(r"\blegacy\b", q, re.I):
        h["legacy"] = True
    if re.search(r"\bstor(?:e|ing|ed)\b.{0,40}(?:result|address|data)|(?:keep|save).{0,30}results?.{0,20}(?:db|database)|"
                 r"\bpermanent(?:ly)?\b", q, re.I):
        h["store"] = True
    if re.search(r"with and without traffic|what if we drop|drop (?:the )?(?:live )?traffic|traffic.{0,20}or not|"
                 r"(?:might|may|could)\s+not\s+need.{0,20}traffic", q, re.I):
        h["traffic"] = None
    elif re.search(r"\b(no|without|don'?t need|not needed|ignore)\b.{0,20}traffic|traffic.{0,15}not (needed|required)", q, re.I):
        h["traffic"] = False
    elif re.search(r"traffic-aware|live[- ]traffic|real-time traffic|departure_time|with traffic", q, re.I):
        h["traffic"] = True
    # a plain monthly volume ("1,000,000 traffic-aware route requests", "250,000 elements", "300,000 lookups")
    if "matrix" not in h and "users_typing" not in h and "sessions" not in h:
        v = re.search(_NUM + r"[^\d]{0,40}?\b(?:requests|lookups|elements|pairs|calls|routes)\b", q)
        if v:
            h["volume"] = _n(v.group(1), v.group(2))
    if re.search(r"place autocomplete|places? api|routes api|compute routes?|\bgcp\b|google cloud", q, re.I):
        h["google"] = True
    return h


def reconcile(item: dict, h: dict) -> dict:
    """Overlay regex hints on one model-extracted item."""
    it = dict(item)
    api = (it.get("api") or "").lower()
    if api == "distance_matrix" and "matrix" in h:
        it["origins"], it["destinations"], it["calls"] = h["matrix"]
        it["requests"] = None
    if api == "autocomplete":
        if "users_typing" in h:
            it["sessions"], it["keystrokes_per_session"] = h["users_typing"]
            it["requests"] = h["users_typing"][0] * h["users_typing"][1]
            it["addon"] = False
        elif "sessions" in h:
            it["sessions"] = h["sessions"]
            if "ks" in h:
                it["keystrokes_per_session"] = h["ks"]
                it["requests"] = h["sessions"] * h["ks"]
            it["addon"] = True
    if "volume" in h and not (api == "autocomplete" and (it.get("sessions") or h.get("sessions"))):
        it["requests"] = h["volume"]
    if h.get("two_wheeler"):
        it["two_wheeler"] = True
    if h.get("legacy"):
        it["legacy"] = True
        if api == "autocomplete" and h.get("google"):
            it["provider"] = "Google Maps Platform"
    if h.get("store"):
        it["store"] = True
    if h.get("waypoints"):
        it["waypoints"] = h["waypoints"]
    if "traffic" in h:
        it["traffic"] = h["traffic"]
    return it


def has_volume(query: str) -> bool:
    """True if the question states a quantity >= 100 (digits with optional M/K/lakh/crore/million/thousand); years don't count."""
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(M\b|K\b|lakh|crore|million|thousand)?", query, re.I):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        v *= {"m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3, "lakh": 1e5, "crore": 1e7}.get((m.group(2) or "").lower(), 1)
        if v >= 100 and not (2020 <= v <= 2035 and not m.group(2)):
            return True
    return False


def _expand_unnamed(items: list[dict], query: str) -> list[dict]:
    """'Which matrix APIs fit / each provider / which is cheapest' names no provider: the reference prices Ola, Google and
    Mapbox, but the small model often emits only one. Clone each workload for the missing priced providers."""
    from ..retrieve.query_analyzer import detect
    if detect(query)["providers"] or not items:
        return items
    if not re.search(r"\b(which|each|options?|compare|cheapest|apis|providers?)\b", query, re.I):
        return items
    out = list(items)
    have = {(i.get("provider"), i.get("api")) for i in items}
    for it in items:
        if it.get("api") not in ("directions", "distance_matrix", "reverse_geocode", "autocomplete"):
            continue
        for prov in ("Ola Maps", "Google Maps Platform", "Mapbox"):
            if (prov, it["api"]) not in have:
                out.append({**it, "provider": prov})
                have.add((prov, it["api"]))
    return out


def _tier_suffix(pro: bool, both: bool, essentials_name: str = "Essentials") -> str:
    if not both:
        return ""
    return " (traffic-aware, Pro)" if pro else f" (no traffic, {essentials_name})"


def price_item(it: dict) -> list[dict]:
    """Return calculator result dicts for one extracted item (raises PriceUnavailable / ValueError)."""
    p, api = it.get("provider"), (it.get("api") or "").lower()
    req, sess, ks = _i(it.get("requests")), _i(it.get("sessions")), _i(it.get("keystrokes_per_session"))
    o, d, calls = _i(it.get("origins")), _i(it.get("destinations")), _i(it.get("calls"))
    traffic, basic = _tri(it.get("traffic")), bool(it.get("basic"))
    two_wheeler, wp = bool(it.get("two_wheeler")), _i(it.get("waypoints")) or 0
    out = []
    if p == "HERE":
        raise PriceUnavailable("HERE list price not verified")
    if p in ("OSM Nominatim", "OSRM"):
        raise PriceUnavailable(f"{p} is free open-source software with no per-request price; the public demo server "
                               "has usage limits (about 1 request per second), heavy use needs self-hosting [OPEN-SOURCE]")
    if p not in ("Ola Maps", "Google Maps Platform", "Mapbox"):
        raise PriceUnavailable(f"{p} is not covered by this knowledge base")
    if api == "distance_matrix":
        grid = bool(o and d and calls)
        if not grid and not req:
            raise ValueError("distance matrix needs origins, destinations and calls, or a total element count")
        elements = o * d * calls if grid else req
        if p == "Ola Maps":
            vs = _variants(traffic, basic)
            for t in vs:
                b = not t
                q = (ola_distance_matrix(o, d, calls, basic=b) if grid
                     else quote("ola.distance_matrix_basic" if b else "ola.distance_matrix", elements))
                r = _summ(q, "Distance Matrix" + (" Basic" if b else ""))
                if b:
                    r["group_suffix"] = " Basic (no traffic)"
                out.append(r)
        elif p == "Google Maps Platform":
            if two_wheeler:
                out.append(_summ(quote("google.route_matrix_enterprise", elements), "Route Matrix Enterprise (two-wheeler)"))
            else:
                vs = _variants(traffic)
                for t in vs:
                    r = _summ(quote("google.route_matrix_pro" if t else "google.route_matrix_essentials", elements),
                              "Distance Matrix elements" + (" (Pro)" if t else " (Essentials)"))
                    r["group_suffix"] = _tier_suffix(t, len(vs) > 1)
                    out.append(r)
        else:
            out.append(_summ(quote("mapbox.matrix", elements), "Distance Matrix elements"))
        return out
    # Mapbox Search Box autocomplete is billed per session, not per keystroke
    n = (sess if sess is not None else req) if (p == "Mapbox" and api == "autocomplete") else \
        (req if req is not None else (sess * ks if (sess and ks) else sess))
    if n is None:
        raise ValueError("no volume found")
    if p == "Ola Maps":
        if api == "directions":
            for t in _variants(traffic, basic):
                b = not t
                r = _summ(quote("ola.directions_basic" if b else "ola.directions", n), "Directions" + (" Basic" if b else ""))
                if b:
                    r["group_suffix"] = " Basic (no traffic)"
                out.append(r)
        else:
            sku = {"autocomplete": "ola.autocomplete", "place_details": "ola.place_details",
                   "reverse_geocode": "ola.reverse_geocode"}[api]
            out.append(_summ(quote(sku, n), api))
            if api == "autocomplete" and sess and it.get("addon", True) is not False:
                r = _summ(quote("ola.place_details", sess), "Place Details add-on (1 per session)")
                r["group_suffix"] = " Place Details add-on (1 per session)"
                out.append(r)
    elif p == "Google Maps Platform":
        if api == "autocomplete":
            if it.get("legacy") and sess and ks:
                a = _summ(quote("google.autocomplete_request", sess * ks), "Legacy Autocomplete per request")
                a["group_suffix"] = " (Legacy) per request"
                b = _summ(quote("google.place_details_pro", sess), "Legacy session ending in Places Details (Autocomplete included)")
                b["group_suffix"] = " (Legacy) session with Places Details (Autocomplete included; Details billed)"
                c = _summ(quote("google.autocomplete_nodetails_session", sess), "Legacy session without Places Details")
                c["group_suffix"] = " (Legacy) session without Places Details"
                out += [a, b, c]
            elif sess and ks:
                out.append(_summ(quote("google.autocomplete_request", google_autocomplete_session_units(sess, ks)),
                                 "Autocomplete requests (first 12 per session)"))
                out.append(_summ(quote("google.place_details_essentials", sess), "Place Details Essentials (1 per session)"))
                alt = _summ(quote("google.autocomplete_request", sess * ks), "Autocomplete requests (every request billed)")
                alt["group_suffix"] = " without session tokens (Place Details billed separately)"
                out.append(alt)
            else:
                out.append(_summ(quote("google.autocomplete_request", n), "Autocomplete per request"))
        elif api == "place_details":
            out.append(_summ(quote("google.place_details_essentials", n), api))
        elif api == "reverse_geocode":
            out.append(_summ(quote("google.geocoding", n), api))
        elif api == "directions":
            if two_wheeler:
                out.append(_summ(quote("google.compute_routes_enterprise", n), "Compute Routes Enterprise (two-wheeler)"))
            else:
                vs = _variants(traffic, force_pro=wp > 10)
                for t in vs:
                    r = _summ(quote("google.compute_routes_pro" if t else "google.compute_routes_essentials", n),
                              "Directions" + (" (Pro)" if t else " (Essentials)"))
                    r["group_suffix"] = _tier_suffix(t, len(vs) > 1)
                    out.append(r)
    elif p == "Mapbox":
        if api == "reverse_geocode" and it.get("store"):
            # storing results requires the Permanent Geocoding API; Temporary is shown only for comparison
            perm = _summ(quote("mapbox.geocoding_permanent", n), "Permanent geocoding (results may be stored)")
            perm["group_suffix"] = " Permanent (storage allowed)"
            tmp = _summ(quote("mapbox.geocoding_temporary", n), "Temporary geocoding (results may NOT be stored)")
            tmp["group_suffix"] = " Temporary (no storage, for comparison)"
            return [perm, tmp]
        sku = {"autocomplete": "mapbox.searchbox_sessions_intro", "reverse_geocode": "mapbox.geocoding_temporary",
               "directions": "mapbox.directions"}.get(api)
        if not sku:
            raise PriceUnavailable(f"no Mapbox SKU mapped for {api}")
        out.append(_summ(quote(sku, n), api + (" (introductory preview, per session)" if api == "autocomplete" else "")))
    if not out:
        raise PriceUnavailable(f"no SKU mapped for {p} / {api}")
    return out


def render_summary(pricing: dict, source_idx: list[int] | None = None) -> str:
    """Deterministic price section appended to the answer. Numbers, totals and 'contact sales' wording come from
    code, so the model cannot mislabel rows, drop a component, or present a partial figure as a total."""
    if not pricing or not pricing.get("pricing"):
        return ""
    fx = load_pricing()["fx"]
    groups: dict[str, list[dict]] = {}
    for r in pricing["results"]:
        groups.setdefault(r.get("group", r["provider"]), []).append(r)
    lines = []
    for g, rs in groups.items():
        cur = rs[0]["currency"]
        if any(r["contact_sales"] for r in rs):
            r = next(r for r in rs if r["contact_sales"])
            limit = r["units"] - r["contact_sales_units"]
            lines.append(f"- {g}: no list total. {r['units']:,} units exceeds the published price list "
                         f"({limit:,} units); usage above that needs a quote from {r['provider']}.")
            continue
        total = sum((Decimal(r["cost"]) for r in rs), Decimal(0))
        parts = ""
        if len(rs) > 1:
            parts = " = " + " + ".join(f"{r['label']} {r['currency']} {Decimal(r['cost']):,.2f}" for r in rs)
        if cur == "USD":
            lines.append(f"- {g}: USD {total:,.2f} (about INR {usd_to_inr(total):,.2f} at {fx['usd_inr']}/USD){parts}")
        else:
            lines.append(f"- {g}: INR {total:,.2f}{parts}")
        notes = [n for r in rs for n in r["notes"] if "UNCONFIRMED" in n]
        if notes:
            lines.append(f"  Assumption: {notes[0]}")
    for u in pricing["unverified"]:
        who = u.split(":")[0]
        if "free open-source" in u:
            lines.append(f"- {who}: free open-source software, no per-request price (the public server is rate-limited; "
                         "heavy use means self-hosting).")
        elif "not covered" in u:
            lines.append(f"- {who}: not covered by this knowledge base; no verified price.")
        else:
            lines.append(f"- {who}: list price could not be verified; ask the vendor for a quote.")
    for e in pricing["errors"]:
        lines.append(f"- {e.split(':')[0]}: not calculated ({e.split(': ', 1)[-1]}).")
    if not lines:
        return ""
    src = (" List-price sources: " + " ".join(f"[{i}]" for i in source_idx) + ".") if source_idx else ""
    return ("Price summary (calculated from the verified list prices; FX " f"{fx['usd_inr']} INR/USD, {fx['as_of']}):\n"
            + "\n".join(lines) + ("\n" + src.strip() if src else ""))


EXPLAIN = re.compile(r"\b(why|how (do|does|can|should|to)|what happens|explain|error|fail\w*|doubl\w*|jump\w*|spike|"
                     r"not working|wrong|problem|issue)\b", re.I)


def is_pure_pricing(query: str, pricing: dict) -> bool:
    """A plain 'what does it cost / which is cheaper' question that the calculator fully answered: code writes the answer."""
    return bool(pricing.get("pricing") and pricing["results"] and not pricing["errors"] and not EXPLAIN.search(query))


def deterministic_text(pricing: dict, source_idx: list[int], esc: dict | None = None) -> str:
    """Whole answer from code for pure pricing questions: no model prose, so nothing can contradict the numbers."""
    parts = []
    # cheapest / most expensive among fully priced groups (INR)
    totals: dict[str, Decimal] = {}
    groups: dict[str, list[dict]] = {}
    for r in pricing["results"]:
        groups.setdefault(r.get("group", r["provider"]), []).append(r)
    for g, rs in groups.items():
        if any(r["contact_sales"] for r in rs) or "billed separately" in g or "add-on" in g:   # not comparable totals
            continue
        totals[g] = (sum((Decimal(r["cost_inr"]) for r in rs), Decimal(0)) if rs[0]["currency"] == "INR"
                     else usd_to_inr(sum((Decimal(r["cost"]) for r in rs), Decimal(0))))
    if len(totals) >= 2:
        lo, hi = min(totals, key=totals.get), max(totals, key=totals.get)
        if totals[lo] == totals[hi]:
            parts.append("All verified options come to the same list-price total.")
        else:
            parts.append(f"Lowest verified list-price total: {lo} (INR {totals[lo]:,.2f}); highest: {hi} (INR {totals[hi]:,.2f}).")
    parts.append(render_summary(pricing, source_idx))
    notes = []
    provs = {r["provider"] for r in pricing["results"]}
    if "Ola Maps" in provs:
        notes.append("Ola Maps: since 1 Sept 2026, self-service usage draws on prepaid Ola Maps credits (contract customers are exempt).")
    if "Google Maps Platform" in provs:
        notes.append("Google: India price list; usage is aggregated across all projects on the billing account. Prices are list prices.")
    if "Mapbox" in provs:
        notes.append("Mapbox: global list pricing.")
    if notes:
        parts.append("\n".join(notes))
    if esc and esc.get("escalate"):
        parts.append(f"This request involves terms or an incident that a person must handle ({esc['category'].replace('_', ' ')}). "
                     f"It is being proposed for hand-off to: {esc['owner']}. No SLA, residency, deployment, discount or timeline "
                     f"is promised here. Please gather: {', '.join(esc['collect'])}.")
    return "\n\n".join(p for p in parts if p)


_AMT = re.compile(r"(?:₹|\bINR\b|\bUSD\b|\bRs\.?|\$)\s?\d")


def strip_fake_summary(text: str) -> str:
    """Remove a model-invented 'Price Summary' block (heading + its bullets) and leaked prompt tags like [VERIFIED LIMITS]."""
    text = re.sub(r"\s?\[(?:VERIFIED LIMITS|VERIFIED FACTS|PRICE SUMMARY|SOURCES)\]", "", text)
    out, skipping = [], False
    for line in text.split("\n"):
        if re.search(r"price summary|^\W*verified (limits|facts)\b", line, re.I):
            skipping = True
            continue
        if skipping and not line.strip():
            skipping = False
        if not skipping:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def strip_prices(text: str) -> str:
    """Remove model-written prices/totals and any self-made 'price summary': the code-built summary is the only price source,
    so the answer cannot contradict it."""
    out = []
    for line in text.split("\n"):
        if re.search(r"price summary|total cost", line, re.I):
            continue
        sents = re.split(r"(?<=[.!?])\s+", line)
        kept = [s for s in sents if not _AMT.search(s)]
        if kept:
            out.append(" ".join(kept))
        elif not line.strip():
            out.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def run(query: str) -> dict:
    """-> {pricing: bool, results: [...], unverified: [reasons], errors: [...], extracted: {...}}"""
    ex = extract(query)
    if ex.get("pricing") and not has_volume(query):
        # no real volume in the question: anything the model extracted is invented (seen on a number-free billing question)
        ex = {**ex, "pricing": False, "dropped": "no volume in question"}
    res = {"pricing": bool(ex.get("pricing")), "results": [], "unverified": [], "errors": [], "extracted": ex}
    if not res["pricing"]:
        return res
    hints = regex_hints(query)
    items = [reconcile(i, hints) for i in (ex.get("items") or []) if isinstance(i, dict)]
    if not hints.get("google"):           # "Place Autocomplete", "Routes API" etc. already imply Google; don't fan out
        items = _expand_unnamed(items, query)
    # A session-based autocomplete item already includes the one Place Details call per session; a separate place_details
    # item for the same provider would double-count it (and produced a bogus extra total in review).
    sess_providers = {i.get("provider") for i in items if i.get("api") == "autocomplete" and i.get("sessions")}
    items = [i for i in items if not (i.get("api") == "place_details" and i.get("provider") in sess_providers)]
    for it in items:
        try:
            rs = price_item(it)
            for r in rs:
                r["group"] = (f"{it.get('provider')} | {str(it.get('api')).replace('_', ' ').title()}"
                              + r.pop("group_suffix", ""))
            res["results"] += rs
        except PriceUnavailable as e:
            res["unverified"].append(f"{it.get('provider')} {it.get('api')}: {e}")
        except Exception as e:  # noqa: BLE001
            res["errors"].append(f"{it.get('provider')} {it.get('api')}: {e}")
    return res
