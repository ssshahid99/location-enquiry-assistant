"""Expected values are the ₹/USD figures in the project ground truth (data/ground_truth/gt.csv)."""
from decimal import Decimal as D

import pytest

from lbs_rag.tools.pricing_calc import (PriceUnavailable, google_autocomplete_session_units, ola_distance_matrix,
                                        quote, usd_to_inr)


def test_ola_reverse_geocode_20m():            # GT-LBS-RG-020
    assert quote("ola.reverse_geocode", 20_000_000).cost_inr == D("860100.00")


def test_ola_reverse_geocode_8m():             # GT-LBS-RG-019
    assert quote("ola.reverse_geocode", 8_000_000).cost_inr == D("560100.00")


def test_ola_place_details_150k():             # GT-LBS-AC-116
    assert quote("ola.place_details", 150_000).cost_inr == D("4950.00")


def test_ola_autocomplete_2_7m():              # GT-LBS-AC-009
    assert quote("ola.autocomplete", 2_700_000).cost_inr == D("145600.00")


def test_ola_free_tier_is_zero():              # GT-LBS-RG-005 (80,000 lookups)
    q = quote("ola.reverse_geocode", 80_000)
    assert q.cost_inr == D("0.00") and q.billable_units == 0 and not q.contact_sales


def test_ola_distance_matrix_50x1_200k_calls():   # GT-LBS-DM-021: 10,000,000 pairs
    assert ola_distance_matrix(50, 1, 200_000).cost_inr == D("1220100.00")
    assert ola_distance_matrix(50, 1, 200_000, basic=True).cost_inr == D("610100.00")


def test_google_reverse_geocode_100k():        # GT-LBS-RG-021
    q = quote("google.geocoding", 100_000)
    assert q.cost == D("45.00") and q.cost_inr == D("4236.31")


def test_google_routes_two_wheeler_50k():      # GT-LBS-DIR-124
    q = quote("google.compute_routes_enterprise", 50_000)
    assert q.cost == D("193.50") and q.cost_inr == D("18216.13")


def test_google_dm_advanced_150k():            # GT-LBS-DM-114
    q = quote("google.distance_matrix_advanced_legacy", 150_000)
    assert q.cost == D("345.00") and q.cost_inr == D("32478.37")


def test_google_autocomplete_per_request_5m():  # GT-LBS-AC-121
    q = quote("google.autocomplete_request", 5_000_000)
    assert q.cost == D("4190.50") and q.cost_inr == D("394494.51")


def test_google_autocomplete_session_200k_x25():  # GT-LBS-AC-121: 2.4M billable + 200k Details Essentials
    units = google_autocomplete_session_units(200_000, 25)
    assert units == 2_400_000
    total = quote("google.autocomplete_request", units).cost + quote("google.place_details_essentials", 200_000).cost
    assert total == D("2175.50") and usd_to_inr(total) == D("204802.01")


def test_mapbox_temporary_geocoding_2m():      # GT-LBS-RG-028: ≈ USD 1,050 for 2,000,000
    q = quote("mapbox.geocoding_temporary", 2_000_000)
    assert q.cost == D("1050.00") and q.cost_inr == D("98847.21")


def test_mapbox_permanent_over_1m_is_contact_sales():   # GT-LBS-RG-028
    q = quote("mapbox.geocoding_permanent", 2_000_000)
    assert q.contact_sales and q.contact_sales_units == 1_000_000


def test_fx_rounding():
    assert usd_to_inr("1.50") == D("141.21")


def test_here_has_no_price():
    with pytest.raises(PriceUnavailable):
        quote("here.matrix", 1000)


def test_zero_units():
    assert quote("ola.reverse_geocode", 0).cost_inr == D("0.00")
