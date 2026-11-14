"""R8: the grounding check wrongly dropped TRUE answers (22 extra abstentions in the 2026-11-09 rerun). Each fix below comes from a real
failure; each has a matching test that unsupported claims are STILL removed, so the check was not simply weakened."""
from lbs_rag.generate import grounding as G

H = [
    {"provider": "Google Maps Platform", "chunk_id": "g1", "text": "Distance Matrix has a maximum of 100 elements per request. Maximum of 25 origins or 25 destinations."},
    {"provider": "Ola Maps", "chunk_id": "o1", "text": "Start free. 100,000 free calls per API every month. Usage-based pricing applies beyond the free allowance."},
    {"provider": "Ola Maps", "chunk_id": "o2", "text": "Ola Maps Autocomplete accepts a strictlocation parameter to restrict suggestions to a region."},
]


def test_numbers_the_customer_wrote_are_not_unsupported():
    q = "Google Distance Matrix returns MAX_ELEMENTS_EXCEEDED for our 15x10 request. Why?"
    ans = "It exceeds the limit because 15 origins and 10 destinations make 150 elements, over the maximum of 100 elements per request [1]."
    r = G.verify(ans, H, set(), query=q)
    assert not r.removed and not r.ungrounded                       # 15, 10 are the customer's; 150 = 15 x 10; 100 is in the passage
    assert G.verify(ans, H, set()).removed                          # without the question, 15 / 150 would have been unsupported


def test_an_invented_number_is_still_removed_even_with_a_question():
    q = "What is the limit for a 15x10 matrix?"
    r = G.verify("The limit is 777 elements per request [1].", H, set(), query=q)
    assert r.removed and r.ungrounded and "number 777" in r.removed_why[0]


def test_products_only_come_from_the_customers_own_numbers():
    assert 150.0 in G.question_numbers("a 15x10 request") and 150.0 not in G.question_numbers("a request") and G.question_numbers(None) == set()


def test_iso_standard_numbers_are_identifiers_not_quantities():
    r = G.verify("Use the ISO 3166 alpha-2 code with the strictlocation parameter to restrict suggestions [3].", H, set())
    assert not r.removed


def test_absence_statements_are_not_claims():
    r = G.verify("According to the sources, there is no mention of raising this limit [1].", H, set())
    assert not r.removed
    r2 = G.verify("Google allows a maximum of 500 elements per request and there is no mention of rate limits [1].", H, set())
    assert r2.removed and r2.ungrounded                             # the absence clause does not whitelist the false clause next to it


def test_true_claim_with_the_wrong_marker_is_repointed_not_dropped():
    r = G.verify("Ola Maps includes 100,000 free calls per API every month [1].", H, set())    # [1] is the Google passage; the Ola one is [2]
    assert not r.removed and r.repointed == 1 and "[2]" in r.text and "[1]" not in r.text


def test_false_claim_is_not_rescued_by_repointing():
    r = G.verify("Ola Maps includes 250,000 free calls per API every month [2].", H, set())
    assert r.removed and r.ungrounded and r.repointed == 0


def test_wrong_provider_claim_is_not_rescued():
    r = G.verify("Mapbox allows 100 elements per request [1].", H, set())
    assert r.removed                                                # names Mapbox; no Mapbox passage anywhere
