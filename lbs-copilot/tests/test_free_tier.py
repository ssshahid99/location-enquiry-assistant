from lbs_rag.tools import free_tier as ft


def test_dm_043_workload_google():                   # GT-LBS-DM-043: 300/day, 2x3 -> 54,000/month; Essentials cap 70,000
    t, provs = ft.answer_text("We send 300 Google Distance Matrix requests a day with 2 origins and 3 destinations. "
                              "Do we stay in the India free cap?")
    assert "54,000" in t and "inside" in t.lower()
    assert "OUTSIDE the cap by 19,000" in t            # Advanced/Pro cap 35,000: reference says 54,000 would exceed it
    assert "Advanced" in t and provs == ["Google Maps Platform"]


def test_which_apis_have_free_allowance_directions():     # GT-LBS-DIR-031
    t, _ = ft.answer_text("Which routing / directions APIs have a free monthly allowance, and how big is it?")
    for needle in ("100,000", "70,000", "35,000", "7,000", "HERE", "OSRM"):
        assert needle in t


def test_which_apis_have_free_allowance_matrix():         # GT-LBS-DM-041
    t, _ = ft.answer_text("Which distance-matrix APIs have a free monthly allowance?")
    assert "100,000" in t and "70,000" in t and "pairs" in t and "unconfirmed" in t.lower()


def test_ola_small_app_inside_free_tier():                # GT-LBS-DIR-032 pattern: ~60,000 requests a month
    t, _ = ft.answer_text("Is Ola Maps Directions free for a small app doing about 60,000 requests a month? Do we need to buy credits?")
    assert "60,000" in t and "inside" in t and "no credits are needed" in t


def test_enterprise_line_only_when_asked():
    t, _ = ft.answer_text("We send 300 Google Distance Matrix requests a day with 2 origins and 3 destinations. Do we stay in the India free cap?")
    assert "Enterprise" not in t
    t2, _ = ft.answer_text("We send 300 Google two-wheeler Distance Matrix requests a day with 2 origins and 3 destinations. Free cap?")
    assert "Enterprise" in t2


def test_has_volume_guard():
    from lbs_rag.tools.pricing_intent import has_volume
    assert has_volume("Ola Reverse Geocoding for 20,000,000 lookups a month") and has_volume("about 2M requests") and has_volume("1.5 lakh calls")
    assert not has_volume("Our Google Distance Matrix bill doubled after we added side_of_road=true. Why?")
    assert not has_volume("As of 2026 can we do this? 5 September")


def test_billing_dispute_is_not_a_free_tier_question():   # GT-LBS-RG-089
    assert ft.answer_text("Our Ola reverse-geocode invoice seems to count calls during the free tier.") is None


def test_plain_price_question_is_not_free_tier():
    assert ft.answer_text("What would Ola Maps Directions cost us for 300,000 requests a month?") is None
