from lbs_rag.generate import grounding as G


def hit(text, provider="Ola Maps"):
    return {"chunk_id": "c", "doc_url": "https://x", "provider": provider, "text": text}


HITS = [hit("Ola Directions allows up to 25 waypoints per request. Rate limiting returns a 429 response when you exceed the limit."),
        hit("Mapbox Directions accepts up to 25 coordinates. The default rate limit is 300 requests per minute.", "Mapbox")]


def test_supported_sentence_is_kept():
    r = G.verify("Ola Directions allows up to 25 waypoints per request [1].", HITS)
    assert r.cited_total == 1 and r.cited_kept == 1 and not r.removed and "25 waypoints" in r.text


def test_invented_number_is_removed_and_reader_is_told():
    r = G.verify("Ola Directions allows up to 25 waypoints [1].\nIt also allows 4500 requests per minute [1].", HITS)
    assert any("4500" in s for s in r.removed) and "4500" not in r.text and G.REMOVED_NOTE in r.text
    assert "25 waypoints" in r.text


def test_numbers_from_calculator_or_verified_config_are_allowed():
    r = G.verify("The monthly total is 859400 [1].", HITS, allowed_numbers={859400.0})
    assert not r.removed


def test_provider_mismatch_is_removed():
    r = G.verify("Google Directions allows up to 25 waypoints [2].", HITS)      # [2] is a Mapbox passage
    assert r.removed and r.ungrounded and r.text == G.UNGROUNDED


def test_nothing_survives_means_honest_could_not_verify():
    r = G.verify("Ola supports 9999 waypoints per request [1].", HITS)
    assert r.ungrounded and "couldn't verify" in r.text and "9999" not in r.text


def test_uncited_lines_and_code_blocks_are_not_judged():
    t = "Intro line with 123456 and no citation.\n```\nGET /x?waypoints=77777 [1]\n```\nOla allows up to 25 waypoints [1]."
    r = G.verify(t, HITS)
    assert "123456" in r.text and "77777" in r.text and not r.removed


def test_content_word_overlap_catches_unrelated_cited_sentence():
    r = G.verify("Satellites transmit atmospheric corrections regularly throughout constellations worldwide [1].", HITS)
    assert r.ungrounded


def test_citation_after_the_full_stop_does_not_hide_an_unsupported_claim():
    junk = "Satellites transmit atmospheric corrections regularly throughout constellations worldwide"
    for text in (junk + ". [1]", junk + " [1].", junk + ".[1]"):
        r = G.verify(text, HITS)
        assert r.ungrounded and r.removed, text
    assert G.normalize_markers("It works. [1][2] Next sentence. [3]") == "It works [1][2]. Next sentence [3]."


def test_the_word_here_is_not_the_provider_HERE():
    r = G.verify("Format the plus codes as shown here and keep 25 waypoints per request [1].", [HITS[0]])
    assert not r.removed                                    # "here" is English; the cited passage is Ola's, which is fine
    bad = G.verify("HERE allows up to 25 waypoints per request [1].", [HITS[0]])
    assert bad.ungrounded                                   # "HERE" written as the provider IS checked against the cited provider


def test_calc_numbers_collects_all_calculator_figures():
    nums = G.calc_numbers([{"output": [{"cost": "45.00", "cost_inr": "4236.31", "units": 100000}]}])
    assert {45.0, 4236.31, 100000.0} <= nums
