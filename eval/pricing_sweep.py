"""Sweep the price calculator against the ground truth (no LLM, no gateway).

For each templated single-provider pricing question we parse the workload with regex, run the calculator, and check that
its totals (INR, and USD where the SKU is in USD) appear among the figures in the reference answer.
A mismatch means one of: calculator/rate error, a rule I don't model, a parse error, or a reference-answer inconsistency.
Usage: python eval/pricing_sweep.py [--split eval|holdout|all]  -> prints summary, writes eval/reports/pricing_sweep.txt
"""
import argparse
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

from lbs_rag.tools.pricing_calc import (google_autocomplete_session_units, ola_distance_matrix,  # noqa: E402
                                        quote)

AMOUNT = re.compile(r"(?:₹|INR\s?|USD\s?|\$)\s?(\d[\d,]*(?:\.\d+)?)")
N = r"(\d[\d,]*(?:\.\d+)?)\s*(M|K)?"


def num(s: str, suf: str | None = None) -> int:
    v = float(s.replace(",", ""))
    return int(v * {"M": 1_000_000, "K": 1_000, None: 1, "": 1}[suf.upper() if suf else None])


def gt_amounts(ans: str) -> set[float]:
    return {float(m.replace(",", "")) for m in AMOUNT.findall(str(ans))}


def vals(q) -> list[tuple[str, float]]:
    if q.contact_sales:                     # beyond the published list: the reference should say "contact sales", no total
        return [(f"{q.sku} contact-sales", -1.0)]
    out = [(f"{q.sku} INR", float(q.cost_inr))]
    if q.currency == "USD":
        out.append((f"{q.sku} USD", float(q.cost)))
    return out


def plan(it: dict) -> list[list[tuple[str, float]]] | None:
    """Return a list of expectation groups; each group is [(label, value)] that must ALL appear in the GT answer.
    None = not a templated case we model."""
    prov, api, q = it["Service Providers"], it["API"], it["Question"]
    ql = q.lower()
    if it["intent"] not in ("pricing_quote", "pricing_estimate", "pricing_comparison"):
        return None
    g = []
    if prov == "Ola Maps":
        if api == "Directions" and "optimi" not in ql:
            m = re.search(rf"for {N} requests", q)
            if not m:
                return None
            n = num(*m.groups())
            g = [vals(quote("ola.directions", n)), vals(quote("ola.directions_basic", n))]
        elif api == "Distance Matrix":
            m = re.search(rf"(\d+) origins × (\d+) destinations about {N} times", q)
            if m:
                o, d, c = int(m.group(1)), int(m.group(2)), num(m.group(3), m.group(4))
                g = [vals(ola_distance_matrix(o, d, c)), vals(ola_distance_matrix(o, d, c, basic=True))]
            else:
                m = re.search(rf"Basic for {N} pairs", q)
                if not m:
                    return None
                g = [vals(quote("ola.distance_matrix_basic", num(*m.groups())))]
        elif api == "Autocomplete":
            m = re.search(rf"{N} users a month typing ~(\d+) characters", q)
            if m:
                g = [vals(quote("ola.autocomplete", num(m.group(1), m.group(2)) * int(m.group(3))))]
            else:
                m = re.search(rf"{N} calls a month", q)
                if not m:
                    return None
                g = [vals(quote("ola.place_details", num(*m.groups())))]
        elif api == "Reverse Geocode":
            m = re.search(rf"for {N} lookups", q)
            if not m:
                return None
            g = [vals(quote("ola.reverse_geocode", num(*m.groups())))]
    elif prov == "Google Maps Platform":
        if api == "Directions":
            m = re.search(rf"for {N} requests a month in India\. Routes have (\d+) stops", q)
            if m:
                n, stops = num(m.group(1), m.group(2)), int(m.group(3))
                pro = "traffic-aware" in ql or stops > 10
                g = [vals(quote("google.compute_routes_pro" if pro else "google.compute_routes_essentials", n))]
            elif "two-wheeler" in ql:
                m = re.search(rf"{N} Google two-wheeler routes", q)
                if not m:
                    return None
                g = [vals(quote("google.compute_routes_enterprise", num(*m.groups())))]
            else:
                return None
        elif api == "Distance Matrix":
            m = re.search(rf"(\d+)×(\d+) matrices, {N} per month", q)
            if m:
                el = int(m.group(1)) * int(m.group(2)) * num(m.group(3), m.group(4))
                g = [vals(quote("google.route_matrix_pro" if "traffic-aware" in ql else "google.route_matrix_essentials", el))]
            elif "two-wheelers" in ql:
                m = re.search(rf"{N} elements", q)
                g = [vals(quote("google.route_matrix_enterprise", num(*m.groups())))] if m else None
                if not m:
                    return None
            else:
                m = re.search(rf"{N} traffic-aware Distance Matrix elements", q)
                if not m:
                    return None
                g = [vals(quote("google.route_matrix_pro", num(*m.groups())))]
        elif api == "Autocomplete":
            m = re.search(rf"{N} sessions(?: a month)?(?:,| with|) ~?(\d+) requests each", q)
            if not m or "legacy" in ql:
                return None
            s, k = num(m.group(1), m.group(2)), int(m.group(3))
            sess_total = quote("google.autocomplete_request", google_autocomplete_session_units(s, k)).cost \
                + quote("google.place_details_essentials", s).cost
            per_req = quote("google.autocomplete_request", s * k)
            g = [[("session total USD", float(sess_total))], vals(per_req)]
        elif api == "Reverse Geocode":
            m = re.search(rf"{N} requests a month", q)
            if not m:
                return None
            g = [vals(quote("google.geocoding", num(*m.groups())))]
    elif prov == "Mapbox":
        if api == "Directions":
            sku = "mapbox.optimization" if "optimization" in ql else "mapbox.directions"
            m = re.search(rf"{N} requests", q)
            if not m:
                return None
            g = [vals(quote(sku, num(*m.groups())))]
        elif api == "Distance Matrix":
            m = re.search(rf"(\d+)×(\d+) matrices, {N} a month", q)
            if not m:
                return None
            g = [vals(quote("mapbox.matrix", int(m.group(1)) * int(m.group(2)) * num(m.group(3), m.group(4))))]
        elif api == "Autocomplete":
            m = re.search(rf"for {N} user sessions", q)
            if not m:
                return None
            g = [vals(quote("mapbox.searchbox_sessions_intro", num(*m.groups())))]
        elif api == "Reverse Geocode":
            m = re.search(rf"{N} (?:a month|lookups|requests)", q)
            if not m or "store" in ql or "db" in ql:
                return None
            g = [vals(quote("mapbox.geocoding_temporary", num(*m.groups())))]
    return g or None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="eval")
    a = ap.parse_args()
    items = load_items(None if a.split == "all" else a.split)
    items = [i for i in items if "," not in str(i["Service Providers"])]
    lines, ok, bad, skipped = [], 0, [], 0
    for it in items:
        try:
            groups = plan(it)
        except Exception as e:  # noqa: BLE001
            lines.append(f"ERROR {it['ID']}: {type(e).__name__}: {e}")
            bad.append(it["ID"])
            continue
        if groups is None:
            skipped += 1
            continue
        have = gt_amounts(it["Answer"])
        miss = []
        for grp in groups:
            m = [(lab, v) for lab, v in grp
                 if not ((re.search(r"contact|sales|quote", str(it["Answer"]).lower()) is not None) if v < 0 else any(abs(v - h) < 0.011 for h in have))]
            # a group passes if all its values are present; USD-only totals are accepted when INR is rounded differently
            if m:
                miss.append(m)
        if len(miss) == len(groups):          # no group fully matched
            bad.append(it["ID"])
            lines.append(f"MISMATCH {it['ID']} [{it['Service Providers']}|{it['API']}] {it['Question']}\n"
                         f"   calc missing: {miss}\n   GT: {str(it['Answer'])[:260]}")
        else:
            ok += 1
    summary = (f"split={a.split} single-provider items={len(items)} modelled={ok + len(bad)} "
               f"match={ok} mismatch={len(bad)} not-modelled(skipped)={skipped}")
    out = ROOT / "eval" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "pricing_sweep.txt").write_text(summary + "\n\n" + "\n".join(lines), encoding="utf-8")
    print(summary)
    for l in lines[:12]:
        print(l)


if __name__ == "__main__":
    main()
