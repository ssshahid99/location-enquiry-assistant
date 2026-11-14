"""Activity 11: the deterministic calculator against HAND-CALCULATED cases (tiers, free quotas, currency conversion, beyond-the-last-tier).

Every expected value below was worked out on paper from the published rates in config/pricing.yaml, not produced by the code. Working shown per case.
FX: 1 USD = 94.1402 INR. USD rates are per 1,000 units. Tiers are marginal; INR is rounded half-up to the paisa at the end.
"""
import json
from decimal import Decimal

import pytest

from lbs_rag.tools import pricing_calc as pc

# (sku, units, cost in the SKU's currency, cost in INR, hand calculation)
CASES = [
    # ---- Ola (INR, per request; 100,000 free) ----
    ("ola.directions", 50_000, "0.00", "0.00", "inside the free 100,000"),
    ("ola.directions", 100_000, "0.00", "0.00", "exactly the free allowance"),
    ("ola.directions", 1_000_000, "179100.00", "179100.00", "900,000 x 0.199"),
    ("ola.directions", 5_000_000, "975100.00", "975100.00", "4,900,000 x 0.199"),
    ("ola.directions", 6_000_000, "1024100.00", "1024100.00", "4,900,000 x 0.199 = 975,100 + 1,000,000 x 0.049 = 49,000"),
    ("ola.directions_basic", 1_000_000, "89100.00", "89100.00", "900,000 x 0.099"),
    ("ola.directions_basic", 2_000_000, "188100.00", "188100.00", "1,900,000 x 0.099"),
    ("ola.autocomplete", 250_000, "8400.00", "8400.00", "150,000 x 0.056"),
    ("ola.place_details", 300_000, "19800.00", "19800.00", "200,000 x 0.099"),
    ("ola.reverse_geocode", 20_000_000, "860100.00", "860100.00", "4,900,000 x 0.099 = 485,100 + 15,000,000 x 0.025 = 375,000"),
    ("ola.distance_matrix", 1_000_000, "179100.00", "179100.00", "900,000 pairs x 0.199"),
    ("ola.distance_matrix_basic", 1_000_000, "89100.00", "89100.00", "900,000 pairs x 0.099"),
    # ---- Google (USD per 1,000; India price list) ----
    ("google.compute_routes_essentials", 70_000, "0.00", "0.00", "free 70,000"),
    ("google.compute_routes_essentials", 1_000_000, "1395.00", "131325.58", "930,000 x 1.50/1000 = 1,395.00 USD; x 94.1402 = 131,325.579"),
    ("google.compute_routes_essentials", 2_000_000, "2895.00", "272535.88", "1,930,000 x 1.50/1000 = 2,895.00; x 94.1402 = 272,535.879"),
    ("google.compute_routes_pro", 1_000_000, "2895.00", "272535.88", "free 35,000; 965,000 x 3.00/1000 = 2,895.00"),
    ("google.compute_routes_enterprise", 100_000, "418.50", "39397.67", "free 7,000; 93,000 x 4.50/1000 = 418.50; x 94.1402 = 37,656.08 + 1,694.5236 + 47.0701 = 39,397.6737"),
    ("google.compute_routes_essentials", 6_000_000, "7775.00", "731940.06",
     "4,930,000 x 1.50/1000 = 7,395 + 1,000,000 x 0.38/1000 = 380 -> 7,775.00; x 94.1402 = 731,940.055 (half-up)"),
    ("google.compute_routes_essentials", 10_000_000, "9295.00", "875033.16", "7,395 + 5,000,000 x 0.38/1000 = 1,900 -> 9,295.00; x 94.1402 = 847,261.8 + 27,771.359 = 875,033.159"),
    ("google.autocomplete_request", 500_000, "365.50", "34408.24", "430,000 x 0.85/1000 = 365.50; x 94.1402 = 34,408.243"),
    ("google.autocomplete_nodetails_session", 100_000, "331.50", "31207.48", "free 35,000; 65,000 x 5.10/1000 = 331.50; x 94.1402 = 31,207.476"),
    ("google.autocomplete_request", 1_200_000, "960.50", "90421.66", "1,130,000 x 0.85/1000 = 960.50; x 94.1402 = 90,421.662"),
    ("google.route_matrix_essentials", 1_000_000, "1395.00", "131325.58", "same rate as compute routes essentials"),
    # ---- Mapbox (USD per 1,000; several tiers) ----
    ("mapbox.directions", 50_000, "0.00", "0.00", "free 100,000"),
    ("mapbox.directions", 1_000_000, "1600.00", "150624.32", "400,000 x 2.00/1000 = 800 + 500,000 x 1.60/1000 = 800; x 94.1402 = 150,624.32"),
    ("mapbox.directions", 5_000_000, "6400.00", "602497.28", "800 + 800 + 4,000,000 x 1.20/1000 = 4,800 -> 6,400.00; x 94.1402 = 602,497.28"),
    ("mapbox.geocoding_permanent", 600_000, "2900.00", "273006.58", "no free tier: 500,000 x 5.00/1000 = 2,500 + 100,000 x 4.00/1000 = 400; x 94.1402"),
    ("mapbox.searchbox_sessions_standard", 10_000, "86.25", "8119.59", "free 2,500; 7,500 x 11.50/1000 = 86.25; x 94.1402 = 8,119.592"),
    ("mapbox.matrix", 300_000, "400.00", "37656.08", "200,000 x 2.00/1000 = 400.00; x 94.1402 = 37,656.08"),
    ("mapbox.geocoding_temporary", 700_000, "420.00", "39538.88", "400,000 x 0.75/1000 = 300 + 200,000 x 0.60/1000 = 120 -> 420.00; x 94.1402 = 39,538.884"),
]


@pytest.mark.parametrize("sku,units,cost,inr,why", CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES])
def test_hand_calculated(sku, units, cost, inr, why):
    q = pc.quote(sku, units)
    assert str(q.cost) == cost, why
    assert str(q.cost_inr) == inr, why
    assert not q.contact_sales


def test_beyond_the_last_published_tier_is_flagged_not_priced():
    # 10,000,001 Google Essentials: first 10,000,000 = 9,295.00 USD (see above); the one extra unit has no published price
    q = pc.quote("google.compute_routes_essentials", 10_000_001)
    assert str(q.cost) == "9295.00" and q.contact_sales and q.contact_sales_units == 1
    assert any("contact sales" in n for n in q.notes)


def test_usd_to_inr_hand_values():
    assert pc.usd_to_inr("1") == Decimal("94.14")            # 94.1402
    assert pc.usd_to_inr(2895) == Decimal("272535.88")       # 272,535.879
    assert pc.usd_to_inr("0.005") == Decimal("0.47")         # 0.470701


def test_ola_distance_matrix_pairs_mode_counts_pairs():
    # 10 x 10 matrices, 10,000 calls = 1,000,000 pairs; free allowance counted in pairs: 900,000 x 0.199 = 179,100
    q = pc.ola_distance_matrix(10, 10, 10_000)
    assert q.units == 1_000_000 and str(q.cost) == "179100.00"
    assert str(pc.ola_distance_matrix(10, 10, 10_000, basic=True).cost) == "89100.00"      # 900,000 x 0.099


def test_ola_distance_matrix_requests_mode_scales_the_free_tier(monkeypatch):
    # if the free allowance counts REQUESTS (unconfirmed): 100,000 requests x 100 pairs = 10,000,000 free pairs.
    # 150,000 calls of 10 x 10 = 15,000,000 pairs -> 5,000,000 billable x 0.199 = 995,000.00
    monkeypatch.setitem(pc.load_pricing()["ola"], "distance_matrix_free_counts", "requests")
    q = pc.ola_distance_matrix(10, 10, 150_000)
    assert q.free_units == 10_000_000 and str(q.cost) == "995000.00"
    assert any("requests" in n for n in q.notes)


def test_session_units_cap_at_12_billed_requests_per_session():
    assert pc.google_autocomplete_session_units(100_000, 8) == 800_000
    assert pc.google_autocomplete_session_units(100_000, 20) == 1_200_000      # only the first 12 per session are billed


def test_unit_helpers():
    assert pc.matrix_elements(25, 25, 4) == 2_500
    assert pc.requests_needed(1_000_000, 50) == 20_000
    assert pc.requests_needed(1_000_001, 50) == 20_001


def test_unknown_sku_has_no_price():
    with pytest.raises(pc.PriceUnavailable):
        pc.quote("here.directions", 1000)


def test_breakdown_adds_up_to_the_total():
    q = pc.quote("mapbox.directions", 5_000_000)
    assert sum(b["cost"] for b in q.breakdown) == Decimal("6400") and q.free_units == 100_000 and q.billable_units == 4_900_000


@pytest.mark.parametrize("sku,units", [(c[0], c[1]) for c in CASES[::3]])
def test_same_input_gives_byte_identical_output(sku, units):
    a = json.dumps(pc.report(pc.quote(sku, units)), sort_keys=True)
    b = json.dumps(pc.report(pc.quote(sku, units)), sort_keys=True)
    assert a == b


@pytest.mark.parametrize("sku,units", [(c[0], c[1]) for c in CASES])
def test_every_output_lists_assumptions_and_source_docs(sku, units):
    r = pc.report(pc.quote(sku, units))
    assert r["assumptions"] and r["source_urls"] and all(u.startswith("https://") for u in r["source_urls"])
    if r["currency"] == "USD":
        assert any("94.1402" in a for a in r["assumptions"])


def test_report_matches_the_quote():
    r = pc.report(pc.quote("google.compute_routes_essentials", 2_000_000))
    assert r["cost"] == "2895.00" and r["cost_inr"] == "272535.88" and r["free_units"] == 70_000 and r["billable_units"] == 1_930_000
