from lbs_rag.tools import rate_card as R
from lbs_rag.tools.pricing_calc import load_pricing


def test_ola_vs_google_directions_without_a_volume_prices_BOTH_providers_from_the_price_list():
    # the real failing question: only Ola was priced; Google was reported as "not provided"
    text, cited = R.answer_text("compare direction api price between ola map and google maps")
    assert cited == ["Ola Maps", "Google Maps Platform"]
    sku = load_pricing()["skus"]
    assert "INR 0.199 per request (INR 199 per 1,000)" in text and "INR 0.099 per request" in text            # Ola, both variants
    assert "USD 1.50 per 1,000 requests" in text and "USD 3.00 per 1,000 requests" in text                   # Google Essentials and Pro
    assert "70,000 requests a month free" in text and "35,000 requests a month free" in text
    assert sku["google.compute_routes_essentials"]["tiers"][1][1] == 1.5                                      # the text matches the config


def test_every_number_comes_from_pricing_yaml_and_google_is_converted_at_the_configured_rate():
    text, _ = R.answer_text("compare direction api price between ola map and google maps")
    assert "about INR 141.21" in text and "94.1402" in text


def test_here_is_never_priced_and_open_source_providers_are_free():
    text, cited = R.answer_text("What is the price of Google Distance Matrix and HERE matrix?")
    assert "HERE" in text and "won't state one" in text and cited == ["Google Maps Platform"]
    t2, _ = R.answer_text("price of OSRM directions versus Ola directions")
    assert "free open-source" in t2


def test_questions_with_a_volume_an_explanation_or_no_api_are_not_rate_cards():
    assert R.answer_text("Ola Directions cost for 2 million requests") is None          # calculator path
    assert R.answer_text("Why does my Google Directions call fail and what does it cost") is None
    assert R.answer_text("What is the price of Google Maps") is None                      # no API named
    assert R.answer_text("Tell me a joke about directions") is None                       # no price language
    assert R.answer_text("My invoice for Ola Directions is higher than expected, price?") is None


def test_unnamed_providers_compare_all_three_priced_ones():
    _, cited = R.answer_text("Which is cheaper for Directions?")
    assert cited == ["Ola Maps", "Google Maps Platform", "Mapbox"]

def test_limit_and_capability_questions_are_not_price_questions():
    # three development questions the first version wrongly caught
    assert R.answer_text("Which autocomplete APIs support session-based pricing?") is None
    assert R.answer_text("What rate limits apply to autocomplete on each provider?") is None
    assert R.answer_text("What are the rate limits for reverse geocoding on each provider?") is None
    assert R.answer_text("What is the rate for Google Directions?") is not None            # a real price-rate question still works
