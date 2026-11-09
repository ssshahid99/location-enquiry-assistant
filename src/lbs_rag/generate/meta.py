"""General / meta questions ("hello", "what can you do", "which questions can I ask", "which providers do you cover",
"what are your limits", "can I trust you"). Answered from config by code: no retrieval, no model call, nothing invented.

Deliberately conservative: a question is treated as meta only if it is short and names no API-specific content, so real
questions ("What can you tell me about Ola pricing?") still go to the RAG pipeline. tests/test_meta.py checks that none of
the 500 ground-truth questions is classified as meta.
"""
import re
from pathlib import Path

from ..tools.pricing_calc import load_pricing

ROOT = Path(__file__).resolve().parents[3]

EXAMPLES = [
    "Ola Maps Reverse Geocoding for 20,000,000 lookups a month. Cost?",
    "Compare Ola Maps and Google for 1,000,000 route requests a month in India.",
    "Which distance-matrix APIs have a free monthly allowance?",
    "Our Google Distance Matrix bill doubled after we added side_of_road=true. Why?",
    "Can we use nominatim.openstreetmap.org for type-ahead search in our app?",
    "Google Place Autocomplete returns INVALID_REQUEST when we pass six types. Why?",
    "Our whole dispatch system is down: Ola Directions returns 429 for 80% of calls.",
]

_GREET = re.compile(r"^\W*(hi|hello|hey|hiya|namaste|good (morning|afternoon|evening)|greetings)\b[\s\W]*(there|team|copilot|assistant)?[\s\W]*$", re.I)
_THANKS = re.compile(r"^\W*(thanks|thank you|thx|ok(ay)? thanks|great,? thanks|cool,? thanks|appreciate it)\b[\s\W\w]{0,30}$", re.I)
_BYE = re.compile(r"^\W*(bye|goodbye|see you|that'?s all|good night)\b[\s\W\w]{0,20}$", re.I)
_PROVIDER_WORDS = re.compile(r"google|mapbox|\bhere\b|\bola\b|krutrim|osrm|nominatim|openstreetmap|\bosm\b", re.I)
_SPECIFIC = re.compile(r"\d|\b(price|pricing|cost|bill|invoice|error|429|500|latency|quota|parameter|endpoint)\b", re.I)

_CAPS = re.compile(
    r"what (can|could|do) (you|this (system|tool|bot|copilot|assistant)|the (system|copilot|assistant|bot)) (do|help|answer|support)|"
    r"how (can|could|do|does) (you|this (system|tool|bot|copilot|assistant)|the (system|copilot|assistant|bot)) (help|work)|"
    r"how (does|do) (this|it)\s+work|"
    r"what (kind|type|sort)s? of (questions|queries|things)|which (kind|type|sort)s? of (questions|queries)|"
    r"what (questions|things|topics) (can|could|may|should) (i|we) (ask|try)|what (can|could|should) (i|we) ask|"
    r"what (are|is) (you|this)\b|who are you|what do you do|tell me about yourself|introduce yourself|"
    r"your capabilit|(sample|example) (questions|queries|prompts)|give me (some )?(examples|ideas)|"
    r"how (do|does|can) (i|we) use (you|this)|how to use (you|this)|what (topics|areas) (do|can) you cover|help me get started|^\W*help\W*$", re.I)
_SCOPE = re.compile(
    r"which (providers|vendors|apis|services|companies|maps) (do|does|can|are) (you|this|the system|covered|supported)|"
    r"what (providers|vendors|apis|services) (do|does|can) (you|this|the system) (cover|support|know|handle)|"
    r"(do|can|does) (you|this|the system) (cover|support|know about|handle) (all )?(providers|vendors)|"
    r"(do|can) you (cover|support|know about|handle)\b.{0,40}(google|mapbox|\bhere\b|\bola\b|osrm|nominatim|tomtom|azure|esri|mappls)", re.I)
_LIMITS = re.compile(
    r"what (can'?t|cannot|can not|don'?t|do not) you|your (limitations|limits|weaknesses)|what are your limits|"
    r"are you (accurate|reliable|correct|always right)|how accurate (are|is) you|can (i|we) trust you|how reliable|"
    r"what (should|shouldn'?t) i not ask|what you (cannot|can'?t|don'?t) (do|know)", re.I)


def classify(query: str) -> str | None:
    q = query.strip()
    if not q or len(q.split()) > 18:
        return None
    if _GREET.match(q):
        return "greeting"
    if _THANKS.match(q):
        return "thanks"
    if _BYE.match(q):
        return "bye"
    if _LIMITS.search(q) and not _SPECIFIC.search(q):
        return "limits"
    if _SCOPE.search(q):
        return "scope"
    if _CAPS.search(q) and not _SPECIFIC.search(q) and not _PROVIDER_WORDS.search(q):
        return "capabilities"
    return None


def corpus_date() -> str:
    raw = ROOT / "data" / "raw"
    try:
        return sorted(p.name for p in raw.iterdir() if p.is_dir())[-1]
    except (OSError, IndexError):
        return "unknown date"


def _fx() -> str:
    fx = load_pricing()["fx"]
    return f"{fx['usd_inr']} INR per USD ({fx['source']}, {fx['as_of']})"


def _examples() -> str:
    return "\n".join(f"- {e}" for e in EXAMPLES)


def _providers() -> str:
    return ("Google Maps Platform, Mapbox, Ola Maps, HERE, OSM Nominatim and OSRM. HERE's list prices could not be verified "
            "(its pricing page did not load), so I will not quote HERE prices. Nominatim and OSRM are free open-source "
            "software; I can explain their usage limits but they have no per-request price.")


def respond(query: str) -> tuple[str, str] | None:
    """-> (text, kind) or None if this is not a general question."""
    kind = classify(query)
    if kind is None:
        return None
    if kind == "greeting":
        return ("Hello! I'm the Location Services Sales & Support Copilot. I answer questions about mapping and location APIs "
                "(pricing, limits, errors, policies) with sources. Ask me something, or type \"what can you do?\" to see what I cover.", kind)
    if kind == "thanks":
        return ("You're welcome. Ask me another question any time.", kind)
    if kind == "bye":
        return ("Goodbye! Come back any time you have a question about location APIs.", kind)
    if kind == "scope":
        return (f"I cover: {_providers()}\n\nTopics: Directions/routing, Distance Matrix, Autocomplete and place search, and reverse "
                "geocoding. For any other provider (for example TomTom, Azure Maps, Esri or Mappls) I will say I have no verified "
                "information.", kind)
    if kind == "limits":
        return (
            "What I can't do, and where to be careful:\n"
            "- I only know the documentation I fetched on " + corpus_date() + ". Newer vendor changes are not reflected.\n"
            "- I do not give HERE prices (not verified) and I decline providers outside my list.\n"
            "- Ola's Distance Matrix free allowance (pairs or requests) is unconfirmed; quotes assume pairs and say so.\n"
            "- I can miss details or misread a question, especially explanatory ones. Check the numbered sources I cite.\n"
            "- I never send emails or contact vendors, and I never promise SLAs, discounts, refunds or timelines. Outages, "
            "security incidents, contract requests and data-quality reports are proposed to a person, who must approve them.\n"
            "- Prices are list prices only; negotiated or contract pricing is not covered.", kind)
    return (
        "I'm the Location Services Sales & Support Copilot. I answer questions about location APIs from verified vendor "
        "documentation, and I show numbered sources for what I say.\n\n"
        "Providers I cover: " + _providers() + "\n\n"
        "I can help with:\n"
        "- Pricing and cost estimates in INR, calculated from the verified price lists (tiers, free allowances, USD to INR at "
        + _fx() + "), and comparisons between providers\n"
        "- Free allowances and whether a usage level stays inside them\n"
        "- Request limits and parameters (waypoints, matrix size, rate limits)\n"
        "- Errors and troubleshooting (for example 429, ZERO_RESULTS, INVALID_REQUEST)\n"
        "- Billing and SKU questions, usage policies (caching, type-ahead) and migration notes\n"
        "- Which API fits a use case\n\n"
        "How I handle risk: I don't invent prices, I say when something isn't verified, and outages, security incidents and "
        "contract or SLA requests are proposed to a person for approval; I never act on them myself.\n\n"
        "Try questions like:\n" + _examples() + "\n\n"
        "Type \"what are your limits?\" to see what I can't do.", kind)
