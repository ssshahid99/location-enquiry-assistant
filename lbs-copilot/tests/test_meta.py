import sys
from pathlib import Path

import pytest

from lbs_rag.generate import meta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

GENERAL = {
    "greeting": ["hi", "Hello!", "hey there", "Good morning", "namaste"],
    "thanks": ["thanks", "Thank you!", "ok thanks"],
    "bye": ["bye", "goodbye"],
    "capabilities": ["what can you do?", "How can the system help me?", "Which kind of questions can I ask?",
                     "what questions can I ask", "who are you", "How can you help us", "give me some example questions",
                     "what is this", "help", "How does this work?"],
    "scope": ["Which providers do you cover?", "what vendors do you support", "Do you cover Mapbox?", "can you support TomTom"],
    "limits": ["what are your limits?", "can I trust you", "What can't you do?", "how accurate are you"],
}


@pytest.mark.parametrize("kind,q", [(k, q) for k, qs in GENERAL.items() for q in qs])
def test_general_questions_are_recognised(kind, q):
    assert meta.classify(q) == kind


REAL = [
    "What can you tell me about Ola Maps pricing?",
    "How can we reduce our Google Maps bill?",
    "Ola Maps Reverse Geocoding for 20,000,000 lookups a month. Cost?",
    "How do I use the Ola Distance Matrix API?",
    "Which providers support walking directions in India?",
    "What are the limits of Google Distance Matrix?",
    "Can I trust Nominatim for production?",
    "What is the free tier for Mapbox Directions?",
    "help me with a 429 error from Ola",
]


@pytest.mark.parametrize("q", REAL)
def test_real_questions_are_not_hijacked(q):
    assert meta.classify(q) is None


def test_no_ground_truth_question_is_classified_as_general():
    from load_gt import load_items
    hits = [(i["ID"], i["Question"]) for i in load_items() if meta.classify(i["Question"])]
    assert hits == [], hits


def test_capability_answer_is_built_from_config_and_mentions_limits():
    text, kind = meta.respond("what can you do?")
    assert kind == "capabilities"
    for needle in ("Google Maps Platform", "Ola Maps", "94.1402", "HERE", "never", "Try questions like"):
        assert needle in text
    assert "TomTom" not in text or "Azure" in text


def test_limits_answer_is_honest_about_gaps():
    text, _ = meta.respond("what are your limits?")
    assert "HERE" in text and "unconfirmed" in text and "SLA" in text
