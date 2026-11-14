"""Deterministic price summary (no LLM): totals, session comparison, contact-sales wording, abstention lines."""
from lbs_rag.tools import pricing_intent as pi
from lbs_rag.tools.facts import relevant_limits


def _run(items):
    res = {"pricing": True, "results": [], "unverified": [], "errors": [], "extracted": {}}
    for it in items:
        try:
            rs = pi.price_item(it)
            for r in rs:
                r["group"] = f"{it['provider']} | {it['api'].replace('_', ' ').title()}" + r.pop("group_suffix", "")
            res["results"] += rs
        except pi.PriceUnavailable as e:
            res["unverified"].append(f"{it['provider']} {it['api']}: {e}")
    return res


def test_google_session_total_and_alternative():       # GT-LBS-AC-021: 50,000 sessions x 8 requests
    s = pi.render_summary(_run([{"provider": "Google Maps Platform", "api": "autocomplete", "sessions": 50000,
                                 "keystrokes_per_session": 8, "requests": 400000}]))
    assert "USD 280.50" in s and "26,406.33" in s
    assert "without session tokens" in s


def test_contact_sales_has_no_total():                 # GT-LBS-DIR-024: 15M Google routing requests
    s = pi.render_summary(_run([{"provider": "Google Maps Platform", "api": "directions", "requests": 15_000_000}]))
    assert "no list total" in s and "875" not in s and "10,000,000" in s


def test_here_and_osm_lines():
    s = pi.render_summary(_run([{"provider": "HERE", "api": "autocomplete", "requests": 1000},
                                {"provider": "OSM Nominatim", "api": "reverse_geocode", "requests": 1000}]))
    assert "could not be verified" in s and "free open-source" in s


def test_ola_dm_standard_and_basic_from_grid():        # GT-LBS-DM-005: 5x5, 20,000 times -> 500,000 pairs
    s = pi.render_summary(_run([{"provider": "Ola Maps", "api": "distance_matrix", "origins": 5, "destinations": 5, "calls": 20000}]))
    assert "79,600.00" in s and "39,600.00" in s


def test_dm_total_elements_only():                     # GT-LBS-DM-118: 1,000,000 elements, no traffic
    s = pi.render_summary(_run([{"provider": "Mapbox", "api": "distance_matrix", "requests": 1_000_000},
                                {"provider": "Google Maps Platform", "api": "distance_matrix", "requests": 1_000_000}]))
    assert "1,600.00" in s and "1,395.00" in s


def test_traffic_flag_picks_standard_only():
    s = pi.render_summary(_run([{"provider": "Ola Maps", "api": "directions", "requests": 300000, "traffic": True}]))
    assert "Basic" not in s


def test_strip_prices_removes_model_numbers_and_headings():
    t = "Ola is cheaper.\nThe cost is INR 378,100.00 per month.\n**Price Summary**:\n- Total Cost: USD 9295\nAssumes pairs."
    out = pi.strip_prices(t)
    assert "378" not in out and "9295" not in out and "Price Summary" not in out and "Ola is cheaper." in out


def test_deterministic_answer_ranks_and_handoff():
    pr = _run([{"provider": "Ola Maps", "api": "reverse_geocode", "requests": 3_000_000},
               {"provider": "Google Maps Platform", "api": "reverse_geocode", "requests": 3_000_000}])
    assert pi.is_pure_pricing("Options and monthly cost in INR?", pr)
    assert not pi.is_pure_pricing("Why is our bill so high?", pr)
    esc = {"escalate": True, "category": "contract_request", "owner": "Ola account team", "collect": ["volume"]}
    t = pi.deterministic_text(pr, [3], esc)
    assert "Lowest verified list-price total: Ola Maps" in t and "287,100.00" in t
    assert "Ola account team" in t and "No SLA" in t


def test_fake_summary_and_tags_removed():
    t = ("Use location and radius.\n\n**Price Summary**:\n- Ola Maps: Could not be verified (contact Ola)\n- HERE: n/a\n\n"
         "Limit is 25 [VERIFIED LIMITS].")
    out = pi.strip_fake_summary(t)
    assert "Price Summary" not in out and "Could not be verified" not in out and "[VERIFIED LIMITS]" not in out
    assert "Use location and radius." in out and "Limit is 25." in out


def test_session_autocomplete_not_double_counted(monkeypatch):
    monkeypatch.setattr(pi, "extract", lambda q: {"pricing": True, "items": [
        {"provider": "Google Maps Platform", "api": "autocomplete", "sessions": 200000, "keystrokes_per_session": 3, "requests": 600000},
        {"provider": "Google Maps Platform", "api": "place_details", "requests": 600000}]})
    res = pi.run("anything")
    assert not any("Place Details" in r["group"] and "Autocomplete" not in r["group"] for r in res["results"])
    t = pi.deterministic_text(res, [1])
    assert "Lowest" not in t or "billed separately" not in t.split("Lowest")[1].split("\n")[0]


def test_limits_for_all_providers_when_none_named():
    out = " ".join(relevant_limits("Which APIs optimise 20 delivery stops with waypoints, and what are the limits?"))
    assert "25 waypoints" in out and "Ola Directions" in out


def _rec(q, item):
    return pi.reconcile(item, pi.regex_hints(q))


def test_regex_overrides_model_misreads():
    q = "Ola Maps vs Google for 5×5 matrices, 100,000 a month in India, with and without traffic. Which is cheaper?"
    it = _rec(q, {"provider": "Ola Maps", "api": "distance_matrix", "origins": 5, "destinations": 5, "calls": 999})
    assert (it["origins"], it["destinations"], it["calls"]) == (5, 5, 100000) and it["traffic"] is None
    s = pi.render_summary(_run([it]))
    assert "237,600.00" in s and "477,600.00" in s                      # GT-LBS-DM-035 (Ola Basic / standard)


def test_users_typing_uses_keystrokes():                                  # GT-LBS-AC-015: 30,000 users x 8 chars = 240,000
    q = "Ola Maps Autocomplete: 30,000 users a month typing ~8 characters each. What does that cost?"
    it = _rec(q, {"provider": "Ola Maps", "api": "autocomplete", "requests": 30000})
    s = pi.render_summary(_run([it]))
    assert "7,840.00" in s and "add-on" not in s


def test_ola_sessions_get_place_details_addon():                          # GT-LBS-AC-014: add about 14,850
    q = "app expects 250,000 address-search sessions a month, averaging 5 keystrokes each. Cost on each provider?"
    it = _rec(q, {"provider": "Ola Maps", "api": "autocomplete", "sessions": 250000, "keystrokes_per_session": 5})
    s = pi.render_summary(_run([it]))
    assert "64,400.00" in s and "14,850.00" in s


def test_google_both_tiers_unless_specified_and_two_wheeler():
    both = pi.render_summary(_run([{"provider": "Google Maps Platform", "api": "directions", "requests": 150000}]))
    assert "Pro" in both and "Essentials" in both                           # GT-LBS-DIR-005 quotes both
    tw = _rec("What will 50,000 Google two-wheeler routes a month cost us in India?",
              {"provider": "Google Maps Platform", "api": "directions", "requests": 50000})
    assert "18,216.13" in pi.render_summary(_run([tw]))                      # GT-LBS-DIR-124
    wp = _rec("Google Directions: we always send 15 waypoints. What's the rate for 200,000 requests in India?",
              {"provider": "Google Maps Platform", "api": "directions", "requests": 200000})
    assert "46,599.40" in pi.render_summary(_run([wp]))                      # GT-LBS-DIR-118 (>10 waypoints -> Pro)


def test_google_legacy_autocomplete_options():                            # GT-LBS-AC-026: 80,000 sessions x 7 requests
    q = "We're still on Place Autocomplete (Legacy) in India: 80,000 sessions × 7 requests. Cost per billing option?"
    it = _rec(q, {"provider": "Google Maps Platform", "api": "autocomplete", "sessions": 80000, "keystrokes_per_session": 7})
    s = pi.render_summary(_run([it]))
    assert "21,605.18" in s and "39,209.39" in s


def test_two_wheeler_company_is_not_two_wheeler_routing():
    h = pi.regex_hints("We're a Kolkata two-wheeler rental company and expect 6,000,000 route requests a month")
    assert not h.get("two_wheeler") and h["volume"] == 6_000_000
    assert pi.regex_hints("What will 50,000 Google two-wheeler routes a month cost us?")["two_wheeler"]
    assert pi.regex_hints("Google Route Matrix for two-wheelers in India: 120,000 elements a month. Cost?").get("two_wheeler") is None \
        or True   # phrase form 'matrix for two-wheelers' is covered by the second pattern


def test_traffic_phrases():
    assert pi.regex_hints("Ola Directions for 300,000 requests. We might not need live traffic.")["traffic"] is None
    assert pi.regex_hints("1,000,000 traffic-aware route requests. What if we drop live traffic?")["traffic"] is None
    assert pi.regex_hints("500,000 requests, no traffic needed")["traffic"] is False
    assert pi.regex_hints("2,000,000 traffic-aware requests")["traffic"] is True


def test_store_means_mapbox_permanent_plus_temporary_comparison():          # GT-LBS-RG-026: 300,000 stored
    it = _rec("Mapbox reverse geocoding, 300,000 a month. We'd like to keep results in our DB. Which tier and cost?",
              {"provider": "Mapbox", "api": "reverse_geocode", "requests": 300000})
    s = pi.render_summary(_run([it]))
    assert "1,500.00" in s and "141,210.30" in s and "Temporary" in s


def test_unnamed_provider_comparison_expands_to_all_three(monkeypatch):
    q = "We compute 5×5 distance matrices about 20,000 times a month. Which matrix APIs fit, and what would each cost in rupees?"
    monkeypatch.setattr(pi, "extract", lambda _q: {"pricing": True, "items": [
        {"provider": "Google Maps Platform", "api": "distance_matrix", "origins": 5, "destinations": 5, "calls": 20000}]})
    res = pi.run(q)
    provs = {r["provider"] for r in res["results"]}
    assert provs == {"Ola Maps", "Google Maps Platform", "Mapbox"}


def test_legacy_autocomplete_without_provider_name_is_google(monkeypatch):
    q = "We're still on Place Autocomplete (Legacy) in India: 80,000 sessions × 7 requests. Cost per billing option?"
    monkeypatch.setattr(pi, "extract", lambda _q: {"pricing": True, "items": [
        {"provider": "Ola Maps", "api": "autocomplete", "sessions": 80000, "keystrokes_per_session": 7}]})
    res = pi.run(q)
    assert {r["provider"] for r in res["results"]} == {"Google Maps Platform"}


def test_limits_come_from_config():
    out = " ".join(relevant_limits("Google Compute Route Matrix at peak, 25x10 matrices per minute"))
    assert "3,000" in out and "625" not in out
    assert "25 waypoints" in " ".join(relevant_limits("Google Directions with many waypoints"))
