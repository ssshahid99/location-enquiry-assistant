"""Scope and ethics guard. Runs BEFORE retrieval or any model call, so a refused request never reaches the model.

Every refusal says plainly that the request is not something this system processes, why, and what it can help with instead.
Rules are transparent regexes (auditable, no model judgement). tests/test_guard.py checks that none of the 500 ground-truth
questions is refused, so legitimate questions (for example 'school-bus tracking provider', 'leaked API key pushed to GitHub',
incident reports) are never blocked.

check(query) -> {"refuse": bool, "category": str|None, "message": str|None}
"""
import re
from functools import lru_cache

HELP_LINE = ("I can help with pricing and cost estimates, free allowances, request limits and parameters, errors and "
             "troubleshooting, billing and policy questions for Google Maps Platform, Mapbox, Ola Maps, HERE, OSM Nominatim "
             "and OSRM. Type \"what can you do?\" for examples.")

# (category, regexes, reason shown to the user). First match wins; order = most serious first.
_RULES: list[tuple[str, list[str], str]] = [
    ("self_harm_or_violence",
     [r"\b(kill|murder|hurt|harm|stab|shoot)\b.{0,30}\b(myself|him|her|them|someone|people|my (wife|husband|ex|boss))\b",
      r"\b(suicide|self[- ]harm|end my life|want to die)\b", r"\b(make|build|plant)\b.{0,20}\b(bomb|explosive|weapon)\b",
      r"\b(terror(ist|ism)?|child (porn|abuse)|human trafficking)\b"],
     "this request involves serious harm, which this system will never help with"),
    ("prompt_injection_or_secrets",
     [r"ignore (all |any |your |the |these )?(previous|prior|above|earlier|system)\b.{0,20}(instructions|rules|prompt|guidelines)",
      r"disregard (all |your |the )?(previous|prior|above|system)?\s*(instructions|rules|prompt)",
      r"(reveal|show|print|repeat|display|leak|tell me|give me|output).{0,40}(system prompt|your (instructions|rules|prompt)|hidden (prompt|instructions))",
      r"(reveal|show|print|display|tell me|give me|output|what('?s| is)).{0,30}(the |your |our )?(api[_ -]?keys?|secret|password|credentials|\.env|access token|certificate)\b(?!.{0,30}(restrict|rotate|leak(ed)?|expos))",
      r"\b(jailbreak|developer mode|do anything now|\bDAN\b)\b", r"you are now (a|an|the)\b", r"pretend (to be|you are|that you)\b",
      r"act as (if you (have|had) no|an? (unrestricted|unfiltered))"],
     "it tries to change my instructions or obtain secrets such as keys or my configuration, which I can't share or override"),
    ("tracking_private_individual",
     [r"\b(track|locate|find|follow|spy on|monitor|trace|stalk)\b.{0,50}\b(my (ex|girlfriend|boyfriend|wife|husband|spouse|partner|neighbou?r|colleague|employee)|someone|a (person|woman|girl|man)|a phone number|his|her)\b.{0,60}\b(location|where|home|house|address|live[sd]?|phone)\b",
      r"\bwithout (them|him|her|their|his) (knowing|consent|permission|noticing)\b", r"\bsecretly\b.{0,30}\b(track|locate|follow|monitor)",
      r"\b(home|residential|house) address of\b.{0,40}\b(person|someone|individual|him|her|my)\b",
      r"\bfind (out )?where (he|she|they|my \w+) (live|lives|is|are)\b"],
     "locating or following a private individual without their consent is a privacy and safety risk; I only support location APIs for legitimate, consented products"),
    ("abuse_or_evasion",
     [r"\b(bypass|circumvent|get around|evade|avoid|trick|defeat|beat)\b.{0,50}\b(rate ?limit|quota|billing|payment|paying|terms of (service|use)|tos\b|licen[cs]e|attribution|free ?tier|usage (limits?|caps?))",
      r"\b(multiple|many|several|fake|throwaway) (accounts?|projects?|api keys?|emails?)\b.{0,60}\b(free|credits?|quota|avoid|rate|reset)",
      r"\b(use|using|get|find|buy|share|crack(ed)?)\b.{0,15}\b(stolen|leaked|cracked|pirated|someone else'?s|somebody else'?s|free)\b.{0,30}\bapi ?keys?\b",
      r"\bscrap(e|ing)\b.{0,50}\b(google maps|places|google results|maps results)\b", r"\bremove (the )?(attribution|watermark|logo)\b.{0,40}(without|avoid|hide)",
      r"\bstore\b.{0,30}\bgoogle\b.{0,40}\b(permanently|forever)\b.{0,30}\b(avoid|so we don'?t|without paying)"],
     "it asks how to get around a provider's limits, billing, licence terms or access controls, which I won't help with. I can explain the legitimate options (higher quotas, caching rules that are allowed, paid plans)"),
    ("deception_in_sales",
     [r"\b(lie|mislead|deceive|trick|fool)\b.{0,50}\b(customer|client|buyer|prospect|user)\b", r"\bhide\b.{0,40}\b(limit|limits|limitation|cost|costs|price|fees?|catch)\b.{0,40}\b(from|so)\b",
      r"\b(make up|invent|fabricate|fake|forge|falsif\w+)\b.{0,40}\b(price|prices|pricing|quote|numbers?|benchmark|citation|source|statistic|testimonial|review)\b",
      r"\bsay (that )?\w+( is| are)? (cheaper|better|faster|free)\b.{0,30}\b(even if|regardless|whether or not)\b"],
     "it asks me to mislead a customer or fabricate prices, benchmarks or sources. I only state verified facts and say when something is unverified"),
    ("forbidden_action",
     [r"\b(send|email|forward)\b\s+(this|that|the|a|an|my|our)?\s*(email|mail|message|quote|reply|response|proposal|invoice|summary|escalation)\b",
      r"\b(approve|reject|sign off on)\b\s+(the |this |that |our )?(escalation|proposed action|case|ticket)\b",
      r"\b(promise|guarantee|commit to|assure)\b.{0,40}\b(sla|uptime|discount|refund|timeline|deadline|fix date|price match)\b",
      r"\b(buy|purchase|top up|pay for)\b.{0,30}\b(credits?|plan|subscription|licen[cs]e)\b.{0,25}\bfor (us|me|our|my)\b"],
     "it asks me to take or promise an action. I never send messages, contact vendors, approve things, make purchases or promise SLAs, discounts, refunds or timelines. I can explain the facts or draft an escalation that a person must approve"),
    ("consumer_navigation",
     [r"\b(best|fastest|shortest|quickest|cheapest) (way|route|path|flight|train|bus)\b.{0,20}\b(from|to|between)\b",
      r"\bhow (do|can|should) i (get|travel|go|reach|drive)\s+(from|to)\b", r"\bgive me (the )?(directions|route) (from|to) [A-Z][a-z]+",
      r"\bwhat'?s the (distance|travel time|drive time) (from|between) [A-Z][a-z]+", r"\b(nearest|closest|best) (restaurant|hotel|atm|petrol pump|hospital|cafe|mall)\b"],
     "this looks like a personal travel or navigation question. I support developers and sales teams working with location APIs; I don't give routes, distances or place recommendations"),
    ("professional_advice",
     [r"\b(is it legal|legal advice|can i be sued|will i be sued|lawsuit|sue (google|ola|mapbox|here))\b",
      r"\b(medical|diagnos(e|is)|prescri(be|ption)|dosage|symptoms?)\b.{0,30}\b(advice|for|of|my)\b",
      r"\b(invest|stock tip|which stock|crypto(currency)?)\b.{0,30}\b(buy|advice|should)\b"],
     "legal, medical and investment advice is outside what I can provide. For licence or legal questions please ask your legal team; I can point to what the vendor's published terms say"),
]

# Off-topic gate. Calibrated on the development split (eval/calibrate_guard.py + a probe set of vague-but-legitimate questions):
# legitimate vague questions score >= 0.506 against our corpus, clearly unrelated ones <= 0.47, but borderline unrelated ones
# (0.51-0.55) overlap with vague legitimate ones, so the similarity cut-off is deliberately conservative (never refuse a real
# question) and an explicit subject list catches the obvious off-topic requests. Set to None to disable the similarity part.
DOMAIN_SIM_MIN: float | None = 0.47

_OFF_TOPIC = re.compile(
    r"\b(weather|forecast|recipes?|cooking|calories|joke|poem|poetry|song lyrics?|lyrics|movies?|films?|cricket|football|ipl|"
    r"horoscope|astrology|capital (city )?of|president of|prime minister|translate|translation|homework|essay|resume|cover letter|"
    r"stocks?|crypto|diet|workout|gym|relationship advice|dating|lottery|exam|syllabus|write (me )?(a|an) (poem|story|essay|song|joke|email|letter)|"
    r"tell me a (joke|story)|meaning of life|who won)\b", re.I)

_STRONG = re.compile(
    r"\b(api|apis|sdk|geocod\w*|reverse[- ]geocod\w*|routing|routes?|directions|distance[- ]?matrix|matrix|autocomplete|autosuggest|waypoints?|"
    r"lat(itude)?|lng|lon(gitude)?|polyline|nominatim|osrm|openstreetmap|osm|mapbox|google maps|gcp|google cloud|ola maps|krutrim|places?|"
    r"place ids?|sku|quota|rate[- ]?limit|session[- ]?token|endpoint|zero_results|invalid_request|over_query_limit|request_denied|"
    r"geofenc\w*|eta|traffic|tiles?|isochrone|map ?matching|pricing|price list|free tier|prepaid credits?|prepaid|credit balance|"
    r"grace period|1 september|september 2026|october 2026)\b|\bHERE\b", re.I)

_SCOPE_PROMPT = """You screen questions for a support copilot about location and mapping APIs (Google Maps Platform, Mapbox, Ola Maps, HERE, OSM Nominatim, OSRM): pricing, free tiers, limits, parameters, errors, billing, API keys and accounts, policies, coverage and accuracy of these APIs, and which API to use.
Customers often ask short or vague questions about these products without naming them.
Answer YES if the question could reasonably be about using, buying, integrating or troubleshooting such an API or its vendor. Answer NO if it is about anything else (general knowledge, science, personal advice, entertainment, writing tasks, travel or navigation for a person, other software).
Question: {q}
Reply with exactly one word: YES or NO.
/no_think"""

BORDERLINE_SIM = 0.65     # below this (and no domain term) we ask the model for a second opinion


def llm_in_scope(query: str) -> bool:
    """Second opinion for borderline questions (no domain term, middling similarity). Measured on 40 probes: 19/20 legitimate vague
    questions YES, 20/20 off-topic NO. Fails OPEN (True) if the gateway errors, so an outage never refuses real questions."""
    try:
        from config.settings import openai_client, settings
        r = openai_client().chat.completions.create(model=settings.chat_model, temperature=0, max_tokens=8,
                                                    messages=[{"role": "user", "content": _SCOPE_PROMPT.format(q=query)}])
        t = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip().upper()
        return not t.startswith("NO")
    except Exception:  # noqa: BLE001
        return True


@lru_cache(maxsize=1)
def _compiled():
    return [(c, [re.compile(p, re.I) for p in pats], why) for c, pats, why in _RULES]


def _message(category: str, why: str) -> str:
    return (f"I can't help with this request: it is not something this system is designed to process, because {why}.\n\n"
            + HELP_LINE)


def off_topic_message() -> str:
    return ("I can't help with this request: it is outside what this system is designed to process. I answer questions about "
            "location and mapping APIs (Google Maps Platform, Mapbox, Ola Maps, HERE, OSM Nominatim, OSRM), not general topics.\n\n"
            + HELP_LINE)


def check(query: str) -> dict:
    """Rule-based screen. The off-topic similarity gate is applied separately by domain_gate() (needs the index)."""
    q = query.strip()
    for category, pats, why in _compiled():
        for p in pats:
            if p.search(q):
                msg = _message(category, why)
                if category == "self_harm_or_violence" and re.search(r"suicide|self[- ]harm|end my life|want to die|myself", q, re.I):
                    msg = ("I'm really sorry you're feeling this way. I can't help with this here, but you don't have to face it alone: "
                           "please reach out to local emergency services or a trusted person right now.\n\n" + HELP_LINE)
                return {"refuse": True, "category": category, "message": msg}
    return {"refuse": False, "category": None, "message": None}


def has_strong_domain_term(query: str) -> bool:
    return bool(_STRONG.search(query))


def domain_gate(query: str, similarity: float | None) -> dict | None:
    """Off-topic check. Refuse only if (a) the gate is calibrated/enabled, (b) the question has no strong domain term, and
    (c) its best similarity to our corpus is below the calibrated threshold. -> refusal dict or None."""
    if has_strong_domain_term(query):
        return None
    if _OFF_TOPIC.search(query):
        return {"refuse": True, "category": "off_topic", "message": off_topic_message()}
    if DOMAIN_SIM_MIN is not None and similarity is not None and similarity < DOMAIN_SIM_MIN:
        return {"refuse": True, "category": "off_topic", "message": off_topic_message()}
    return None
