"""The Planner: for every chat message it picks exactly ONE route and says why. Rules decide first; a small model call only when unclear.

  refuse       the scope / ethics guard refuses (not a route: nothing is retrieved, nothing is generated)
  escalation   a person must handle this (incident, security, billing outage, contract request, or the customer asks for a person)
  llm          conversation only: greetings, what-can-you-do, short follow-ups answered from the conversation, clarifying questions.
               It never states prices, limits, errors or parameters; those need documents.
  rag          anything that needs a fact or a number from the documents (and the calculator when a volume is given)

Safety first: an escalation rule match is never overridden by the model. Any invalid model output falls back to rag (grounded and cited).
`extra_tools` lists tools that run in addition to the route (update_record when the message names a sandbox TKT-/OPP- record).
"""
import json
import os
import re

from config.settings import openai_client, settings

from ..agent import planner as case_rules          # rule-based classification and fact extraction (no model)
from ..generate import guard, meta
from ..retrieve.query_analyzer import PROVIDER_KEYWORDS, detect
from ..safety import escalation
from ..tools.pricing_intent import has_volume

ROUTES = ("llm", "rag", "escalation")
RECORD_ID = re.compile(r"\b(?:TKT|OPP)-\d{3,8}\b")
_PERSON = re.compile(r"\b(?:talk|speak|connect me|transfer me|hand (?:me )?over) to (?:a |an |the )?(?:human|person|agent|someone|representative|"
                     r"support (?:team|engineer)|account (?:manager|team))\b|\bhuman (?:agent|support)\b|\bescalate (?:this|it|my)\b|"
                     r"\braise (?:a |an )?(?:ticket|incident)\b", re.I)
_CONTINUE = re.compile(r"\b(?:proceed|go ahead|carry on|not sure|don'?t know|do not know|no idea|undecided|not decided|haven'?t decided|"
                       r"assume|you decide|whatever|either (?:is|way)|skip (?:that|it))\b", re.I)
_FOLLOW = re.compile(r"^\s*(?:and|what about|how about|also|ok(?:ay)?,? (?:and|what about)|same (?:for|with)|what if)\b", re.I)

_PROMPT = """You route one customer message in a chat about location APIs (Google Maps Platform, Mapbox, HERE, Ola Maps, Mappls, OSRM, Nominatim).
Pick exactly one route:
- "rag": the message needs a fact, price, limit, parameter, error cause or comparison from documentation.
- "llm": only conversation: thanks, small talk, a short follow-up that needs no new fact, or the customer should be asked for more details first.
- "escalation": the customer wants a person, reports an outage, a leaked key, a billing dispute or a contract/SLA request.
Reply ONLY JSON: {{"route": "rag|llm|escalation", "reason": "<one short sentence>"}}
Conversation so far: {summary}
Message: {message}
/no_think"""


def _ask_model(message: str, summary: str, client=None) -> dict | None:
    try:
        c = client or openai_client()
        r = c.chat.completions.create(model=settings.chat_model, temperature=0, max_tokens=80,
                                      messages=[{"role": "user", "content": _PROMPT.format(summary=summary or "(none)", message=message)}])
        txt = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S)
        d = json.loads(re.search(r"\{.*\}", txt, flags=re.S).group(0))
        if d.get("route") in ROUTES:
            return {"route": d["route"], "reason": str(d.get("reason", ""))[:160]}
    except Exception:  # noqa: BLE001 - routing must never crash the chat
        return None
    return None


_WEAK_ESC = re.compile(r"\b(?:urgent|asap|outage|down|degraded|degrad\w+|5\d\d|429|invoice|overcharg\w+|leak\w*|stolen|compromis\w+|abus\w+|bot|"
                       r"wrong|incorrect|inaccurate|offend\w*|regress\w+|worse|sla|contract|committed[- ]use|enterprise|residency|negotiat\w+|"
                       r"credits?|balance|stopped|blocked|suspended|bill(?:ed|ing)?)\b", re.I)

_ESC_PROMPT = """Decide whether a customer message must be handed to a person at the provider or our account team. Answer "none" for everything that \
can be answered from documentation: how-to and parameter questions, price or quota questions, documented error codes and their fixes, capability \
questions, billing questions that documentation explains. A single error code (429, 403, 5xx) during a batch job or marketing push, "results look \
inaccurate", "bill doubled", "limit reached" and "will calls stop?" are documented behaviour: answer "none". When in doubt answer "none".
Categories (escalate ONLY when the message itself states the impact):
- production_incident: the customer says their live production is down or badly degraded right now (all or most requests failing, a system outage, customers affected).
- security_incident: an API key leaked or exposed, abuse, a bot attack, an unexpected huge bill caused by abuse.
- billing_incident: the service stopped because credits or balance ran out, or a billing outage.
- data_quality: wrong, offensive or outdated map data (address, pin code, place name) that the provider must correct.
- accuracy_regression: the customer reports a sustained, measured drop in routing / ETA / geocoding accuracy compared with before (for example "ETAs became 20% worse since last month").
- contract_request: a custom, committed-use or enterprise contract, an SLA, data-residency commitments or negotiated pricing.
Reply ONLY JSON: {{"category": "<one of the categories or none>", "reason": "<one short sentence>"}}
Message: {message}
/no_think"""
# OFF by default: measured on the working pool it added 5 false escalations (precision 0.62, target 0.80) and recovered none of the escalations the
# rules miss on the core scoreboard (recall 0.50 either way). Set LBS_ESC_MODEL=1 to try it again after the rules or the prompt change.
MODEL_ESCALATION = os.environ.get("LBS_ESC_MODEL", "0") == "1"
ESC_CATEGORIES = ("production_incident", "security_incident", "billing_incident", "data_quality", "accuracy_regression", "contract_request")


def _ask_escalation(message: str, client=None) -> dict | None:
    """The 'LLM for ambiguous cases' half of the escalation router: only called when the rules did not fire but the message has incident-like words."""
    try:
        c = client or openai_client()
        r = c.chat.completions.create(model=settings.chat_model, temperature=0, max_tokens=80,
                                      messages=[{"role": "user", "content": _ESC_PROMPT.format(message=message[:1500])}])
        txt = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S)
        d = json.loads(re.search(r"\{.*\}", txt, flags=re.S).group(0))
        if d.get("category") in ESC_CATEGORIES:
            return {"category": d["category"], "reason": str(d.get("reason", ""))[:160]}
    except Exception:  # noqa: BLE001
        return None
    return None


def compose_follow_up(last_q: str, message: str) -> str:
    """"and for Mapbox?" after "free quota for Ola Directions?" -> "free quota for Mapbox Directions?" (one stand-alone question: searching the
    two texts glued together just retrieves the old provider again). If the follow-up names no provider, the two are joined."""
    new, old = detect(message)["providers"], detect(last_q)["providers"]
    if not (new and old) or set(new) == set(old):
        return f"{last_q}\nFollow-up: {message}"
    q = last_q
    for p in old:
        for kw in PROVIDER_KEYWORDS[p]:
            q = re.sub(kw, "@@", q, flags=re.I)
    q = re.sub(r"(?:@@\s*)+(?:Maps(?: Platform)?\s+)?", " and ".join(new) + " ", q)
    return re.sub(r"\s+", " ", q).strip()


def _extra_tools(message: str) -> list[str]:
    return ["update_record"] if RECORD_ID.search(message) else []


def plan(message: str, state: dict | None = None, *, client=None, use_model: bool = True) -> dict:
    """-> {route, reason, source: rule|model|fallback, query, extra_tools, signals}"""
    state = state or {}
    base = {"query": message, "extra_tools": _extra_tools(message), "signals": {}}
    g = guard.check(message)
    if g["refuse"]:
        return {**base, "route": "refuse", "reason": f"guard: {g['category']}", "source": "rule", "signals": {"guard": g["category"]}}
    esc = escalation.check(message)
    if esc["escalate"]:
        return {**base, "route": "escalation", "reason": f"escalation rule: {esc['category']}", "source": "rule",
                "signals": {"category": esc["category"], "matched": esc["matched"]}}
    if _PERSON.search(message):
        return {**base, "route": "escalation", "reason": "the customer asked for a person", "source": "rule",
                "signals": {"category": "human_request"}}
    if use_model and MODEL_ESCALATION and _WEAK_ESC.search(message) and not meta.classify(message):
        amb = _ask_escalation(message, client)
        if amb:
            return {**base, "route": "escalation", "reason": f"model judged ambiguous case: {amb['category']} ({amb['reason']})", "source": "model",
                    "signals": {"category": amb["category"]}}
    kind = meta.classify(message)
    if kind:
        return {**base, "route": "llm", "reason": f"general question about the system ({kind})", "source": "rule", "signals": {"meta": kind}}
    # a reply to a question we asked: answer the ORIGINAL question now that the details are known
    pending = state.get("pending")
    last_rag_q = (pending or {}).get("question") or next(
        (m["content"] for m in reversed(state.get("history") or []) if m["role"] == "customer" and m.get("route") == "rag"), None)
    if last_rag_q and _CONTINUE.search(message) and "?" not in message and len(message.split()) <= 40:
        # "I don't know yet, go ahead": the customer declined to give more detail. Answer with what is known and do not ask again.
        return {**base, "route": "rag", "reason": "the customer asked us to proceed with what is known: answering, not asking again",
                "source": "rule", "query": f"{last_rag_q}\nCustomer reply: {message}", "signals": {"declined": True}}
    if pending and state.get("asked") and "?" not in message:
        new = case_rules.extract_facts(message)
        if new:
            q = f"{pending['question']}\nCustomer details: {message}"
            return {**base, "route": "rag", "reason": "the customer answered our clarifying questions: answering the original question",
                    "source": "rule", "query": q, "signals": {"answered": sorted(new)}}
    d = detect(message)
    signals = {"providers": d["providers"], "apis": d["apis"], "volume": has_volume(message), "domain": guard.has_strong_domain_term(message)}
    # a short follow-up ("and for Google?") continues the last documented question
    last_q = next((m["content"] for m in reversed(state.get("history") or []) if m["role"] == "customer" and m.get("route") == "rag"), None)
    if last_q and _FOLLOW.search(message) and len(message.split()) <= 14:
        return {**base, "route": "rag", "reason": "short follow-up to the previous documented question", "source": "rule",
                "query": compose_follow_up(last_q, message), "signals": signals}
    if RECORD_ID.search(message) or signals["providers"] or signals["apis"] or signals["volume"] or signals["domain"] \
            or case_rules.mentions_pricing(message):
        return {**base, "route": "rag", "reason": "needs facts from the documentation", "source": "rule", "signals": signals}
    if use_model:
        m = _ask_model(message, state.get("summary", ""), client)
        if m:
            return {**base, **m, "source": "model", "signals": signals}
    return {**base, "route": "rag", "reason": "unclear: the grounded route is the safe default", "source": "fallback", "signals": signals}
