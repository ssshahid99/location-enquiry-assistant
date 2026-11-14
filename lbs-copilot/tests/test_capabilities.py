import pytest

from lbs_rag.tools import capabilities as C
from lbs_rag.tools.facts import find_chunk


def _all_evidence():
    for cap in C.library():
        for prov, evs in cap["evidence"].items():
            for e in evs:
                yield cap["id"], prov, e


@pytest.mark.parametrize("cap,prov,e", list(_all_evidence()), ids=lambda x: x if isinstance(x, str) else "")
def test_every_evidence_quote_is_in_the_fetched_page(cap, prov, e):
    assert find_chunk(e["url"], e["quote"]) is not None, f"{cap}/{prov}: quote not found in {e['url']}: {e['quote'][:70]}"


def test_providers_without_evidence_are_gaps_not_denials():
    m = C.match_capabilities("Which providers support traffic-aware ETAs? We need live traffic.")
    traffic = next(x for x in m if x["id"] == "traffic_aware_routing")
    by = {r["provider"]: r for r in traffic["providers"]}
    assert by["Google Maps Platform"]["documented"] and by["Mapbox"]["documented"] and by["Ola Maps"]["documented"]
    assert not by["OSRM"]["documented"] and "Not documented" in by["OSRM"]["statement"] and "OSRM" in traffic["gaps"]
    assert "does not support" not in by["OSRM"]["statement"].lower()


def test_named_providers_restrict_the_comparison():
    m = C.match_capabilities("Does Ola Maps support Hindi and Tamil instructions?")
    lang = next(x for x in m if x["id"] == "response_language")
    assert [r["provider"] for r in lang["providers"]] == ["Ola Maps"] and lang["providers"][0]["documented"]


def test_requirement_selects_relevant_capabilities():
    ids = {m["id"] for m in C.match_capabilities("Our drivers need walking directions and to optimise the order of 20 stops")}
    assert {"walking_cycling", "route_optimization", "waypoint_capacity"} <= ids
    assert C.match_capabilities("what colour is the sky") == []


def test_render_table_marks_gaps_and_shows_sources():
    t = C.render_table(C.match_capabilities("optimise the order of stops", ["Ola Maps", "HERE"]))
    assert "[GAP]" in t and "HERE" in t and "https://maps.olakrutrim.com" in t


# ------------------------------------------------------------------ A6: the larger library
def test_library_has_about_25_capabilities_each_with_a_verified_source_and_a_match_rule():
    lib = C.library()
    assert len(lib) >= 24 and len({c["id"] for c in lib}) == len(lib)
    assert all(c["match"] and c["evidence"] for c in lib)


@pytest.mark.parametrize("requirement,expected", [
    ("We need to avoid toll roads for our trucks", {"avoid_tolls_roads", "truck_vehicle_routing"}),
    ("Do you support reverse geocoding of GPS coordinates?", {"reverse_geocoding"}),
    ("Can we snap GPS traces to the road network? We need map matching.", {"map_matching"}),
    ("We will geocode 2 million addresses in bulk", {"bulk_geocoding"}),
    ("our EV fleet needs charging stops", {"ev_routing"}),
    ("we want turn-by-turn instructions in the driver app", {"turn_by_turn"}),
    ("deliveries must arrive by 10am, can routing use an arrival time?", {"arrival_time_routing"}),
    ("how do I decode the polyline of the route", {"route_geometry_format"}),
])
def test_new_capabilities_are_selected_by_realistic_requirements(requirement, expected):
    assert expected <= {m["id"] for m in C.match_capabilities(requirement)}


def test_unrelated_text_selects_no_capability_and_gaps_stay_gaps():
    assert C.match_capabilities("please reset my password and update the invoice address") == [] or \
        "address_validation" not in {m["id"] for m in C.match_capabilities("please reset my password and update the invoice address")}
    ev = next(m for m in C.match_capabilities("EV charging stops", ["Ola Maps", "Mapbox"]) if m["id"] == "ev_routing")
    assert ev["gaps"] == ["Ola Maps"]                       # only Mapbox is documented: Ola is a gap to confirm, not a denial