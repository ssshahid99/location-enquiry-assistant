import pytest

from lbs_rag.agent import executor as E
from lbs_rag.tools.facts import find_chunk

ENTRIES = E.documented_required_params()


def _quotes():
    for e in ENTRIES:
        for p in e["params"]:
            yield e["provider"], e["api"], e["url"], p["names"][0], p["quote"]


@pytest.mark.parametrize("prov,api,url,name,quote", list(_quotes()), ids=lambda x: x if isinstance(x, str) and len(x) < 30 else "")
def test_every_required_parameter_quote_is_in_the_fetched_page(prov, api, url, name, quote):
    assert find_chunk(url, quote) is not None, f"{prov}/{api}/{name}: quote not found in {url}"


def check(provider, apis, msg):
    given = {m.lower() for m in __import__("re").findall(r"\b([A-Za-z_][\w-]*)=[^\s&,;]+", msg)}
    return E._documented_required_check(provider, apis, msg, given)


def test_mapbox_directions_path_style_request_is_complete():
    r = check("Mapbox", ["Directions"], "GET /directions/v5/mapbox/driving/77.59,12.97;77.61,12.93?access_token=XXX returns 422")
    assert r[0]["ok"] is True


def test_mapbox_directions_missing_pieces_are_named_with_source():
    r = check("Mapbox", ["Directions"], "Directions call with alternatives=true fails")
    assert r[0]["ok"] is False and "profile" in r[0]["detail"] and "coordinates" in r[0]["detail"] and "docs.mapbox.com" in r[0]["detail"]


def test_secrets_are_never_reported_missing():
    r = check("Mapbox", ["Directions"], "mapbox/driving 77.59,12.97;77.61,12.93 fails")
    assert r[0]["ok"] is True and "access_token" not in r[0]["detail"]


def test_here_matrix_accepts_either_region_or_profile():
    ok = check("HERE", ["Distance Matrix"], "matrix POST with origins=[...] destinations=[...] profile=carFast")
    bad = check("HERE", ["Distance Matrix"], "matrix POST with origins=[...] destinations=[...]")
    assert ok[0]["ok"] is True and bad[0]["ok"] is False and "regionDefinition or profile" in bad[0]["detail"]


def test_reverse_geocoding_entries_only_apply_to_reverse_requests():
    assert check("Google Maps Platform", ["Reverse Geocode"], "geocoding request with address=Pune fails")[0]["ok"] is None
    r = check("Google Maps Platform", ["Reverse Geocode"], "reverse geocoding request with latlng=18.5,73.8 fails")
    assert r[0]["ok"] is True


def test_providers_or_apis_without_a_verified_list_are_unknown_not_guessed():
    r = check("Google Maps Platform", ["Directions"], "Directions call with foo=bar fails")
    assert r[0]["ok"] is None and "No verified required-parameter list" in r[0]["detail"]
    r = check("OSRM", ["Directions"], "route call foo=bar")
    assert r[0]["ok"] is None


def test_wired_into_check_inputs():
    f = [x for x in E.check_inputs("Mapbox Directions call with alternatives=true fails") if x["check"] == "required_params"]
    assert f and f[0]["ok"] is False
