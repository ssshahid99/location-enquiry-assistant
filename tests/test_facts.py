"""Every fact must be traceable: its verbatim quote has to exist in the fetched vendor page."""
import pytest

from lbs_rag.tools import facts as F


@pytest.mark.parametrize("fact", F._facts(), ids=lambda f: f["id"])
def test_every_quote_is_in_the_fetched_source(fact):
    assert fact["sources"], "a fact without a source is not allowed"
    for s in fact["sources"]:
        assert F.find_chunk(s["url"], s["quote"]) is not None, f"quote not found in {s['url']}: {s['quote'][:70]}"


POSITIVE = {
    "gcp-session-same-project": "Our Place Autocomplete and Place Details calls use API keys from two different GCP projects. Does it matter?",
    "ola-grace-period": "We're on Ola's prepaid credits and our balance hit zero on 5 September 2026. Will Directions stop?",
    "google-reverse-geocode-result-type": "Google reverse geocode with result_type=street_address gives ZERO_RESULTS for a rural point",
    "google-autocomplete-types-max-five": "Google Place Autocomplete returns INVALID_REQUEST when we pass types=restaurant|cafe|bakery|bar|meal_takeaway|meal_delivery.",
    "google-units-text-only": "Google Directions shows '12.4 mi' text for some users even though our app is Indian. And are distance values in miles too?",
    "google-directions-legacy-new-projects": "In our new GCP project, 'Directions API' can't be enabled in the API library. Our old project still works fine. Why?",
    "nominatim-no-client-autocomplete": "Can we use nominatim.openstreetmap.org for type-ahead search in our app?",
    "google-location-modifiers-billing": "Our Google Distance Matrix bill doubled after we added side_of_road=true. Why?",
    "google-place-ids-cacheable": "Can we cache Google place IDs from autocomplete for later Details calls?",
    "ola-rate-limit-429": "Our whole dispatch system is down: Ola Directions returns 429 for 80% of calls since the start of the evening peak.",
    "walking-cycling-support": "Do all the routing providers support walking and cycling directions in India?",
}
NEGATIVE = [
    "What would Ola Maps Directions cost us for 300,000 requests a month?",
    "How do I restrict Ola Autocomplete to Hyderabad only?",
    "Mapbox driving-traffic ETAs in a tier-3 town look identical to the plain driving profile. Why?",
    "Quote Google Maps Platform routing for 15,000,000 requests a month in India.",
]


@pytest.mark.parametrize("fid,q", POSITIVE.items())
def test_trigger_fires_on_its_question(fid, q):
    assert fid in {f["id"] for f in F.matching_facts(q)}


@pytest.mark.parametrize("q", NEGATIVE)
def test_no_fact_fires_on_unrelated_questions(q):
    assert F.matching_facts(q) == []


def test_attach_adds_source_chunks_and_citations():
    hits: list[dict] = []
    lines = F.attach_facts(hits, F.matching_facts(POSITIVE["gcp-session-same-project"]))
    assert hits and lines and "[1]" in lines[0]
    assert "same Google Cloud Console project" in hits[0]["text"]
