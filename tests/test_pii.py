import sys
import pytest
from pathlib import Path

from lbs_rag.safety.pii import redact, redact_with_report

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))


def test_email_phone_aadhaar_pan():
    s, f = redact_with_report("Mail ravi.k@example.com or call +91 98765 43210? PAN ABCDE1234F, Aadhaar 1234 5678 9012")
    assert "ravi.k@example.com" not in s and "ABCDE1234F" not in s and "1234 5678 9012" not in s
    assert {"EMAIL", "PAN", "AADHAAR"} <= set(f)


@pytest.mark.parametrize("phone", ["9876543210", "98765 43210", "98765-43210", "+91 98765 43210", "+91-9876543210", "0091 98765 43210", "+919876543210"])
def test_indian_phone_numbers_in_common_formats_are_redacted(phone):
    s, f = redact_with_report(f"call me on {phone} tomorrow")
    assert "PHONE" in f and "98765" not in s and "tomorrow" in s


def test_prices_and_counts_are_not_mistaken_for_phone_numbers():
    q = "Ola Directions for 98,765 requests, 7,654,321 pairs, 100000 calls, INR 1234567890.50, pin 560001, 8 1234 5678"
    assert "PHONE" not in redact_with_report(q)[1]


def test_url_slugs_are_not_mistaken_for_api_keys():
    t = "see https://developers.google.com/maps/api-security-best-practices and https://example.com/key-restrictions-explained-in-detail"
    assert redact(t) == t
    assert "sk-lm4" not in redact("our key sk-lm4-FAKE0KEY1234567890abcdefXYZ leaked")


def test_api_keys_redacted_but_question_still_reads():
    s = redact("We pushed api_key=AbCdEf1234567890XyZ99 and sk-lm4-FAKE0KEY1234567890abcdefXYZ to GitHub")
    assert "AbCdEf1234567890XyZ99" not in s and "sk-lm4" not in s and "api_key=" in s


def test_google_and_mapbox_keys():
    assert "AIza" not in redact("key AIzaSyA1234567890abcdefghijklmnopqrstuv")
    assert "pk.eyJ" not in redact("token pk.eyJ1IjoiZm9vIiwiYSI6ImNsMTIzNDU2Nzg5MCJ9.abcdefghij")


def test_credit_card_luhn():
    assert "[CARD]" in redact("card 4111 1111 1111 1111 was charged")
    assert "[CARD]" not in redact("order 1234 5678 1234 5678 ref")          # fails Luhn


def test_pricing_numbers_untouched():
    q = "Ola Distance Matrix 10,000,000 pairs; Google 12.97,77.59 at 94.1402 INR; 3000000 requests"
    assert redact(q) == q


def test_ground_truth_questions_rarely_change():
    from load_gt import load_items
    items = load_items()
    changed = [i["ID"] for i in items if redact(i["Question"]) != i["Question"]]
    assert len(changed) <= 5, changed        # only genuine key/IP/email mentions may change
