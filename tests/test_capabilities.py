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
