"""Model-assisted fallback for the Planner (checklist A5).

The rules classify most messages. When they find NO workflow ("unclear"), this asks the model for ONE label and checks it with code.
What the model can and cannot do:
  - it only picks from the fixed list of workflows and categories; anything else is discarded,
  - it never extracts facts, never recommends or triggers an escalation, never creates an approval and never sees case data
    (those stay rules/code, so the human-approval and safety behaviour is unchanged),
  - a low-confidence, malformed or failed answer leaves the message "unclear" exactly as the rules left it,
  - it is OFF by default (config/planner.yaml `model_fallback.enabled`); turn it on only if the tester messages show the rules missing
    real cases. The result is marked source="model" in the plan and the trace so a reviewer can always see it.
The message passed in has already had personal data and secrets removed by the orchestrator.
"""
import json
import re

from config.settings import openai_client, settings

CATEGORIES = {
    "support": {
        "outage": "service down or 5xx errors", "rate_limit": "429 / quota / throttling", "auth": "invalid key, 401/403, REQUEST_DENIED",
        "billing": "unexpected charges or invoice problems", "data_quality": "wrong or inaccurate results", "coverage": "no results or unsupported area",
        "invalid_request": "400/422 or malformed request", "performance": "slow responses or latency",
        "troubleshooting": "something does not work and the cause is unknown", "how_to": "how to use a parameter or feature",
        "security": "leaked key, abuse or compromise",
    },
    "sales": {
        "pricing": "price, cost or free allowance", "competitive": "compare providers", "migration": "move from one provider to another",
        "licensing": "terms of use or licensing", "contract": "SLA, negotiation, custom plan, data residency",
        "discovery": "customer is exploring what to build or use", "capability_match": "does a provider support a feature",
    },
}
VALID_WORKFLOWS = {"support", "sales"}
_PROMPT = """Classify ONE customer message to a location-API (maps, routing, geocoding) provider's support and sales desk.
Reply with JSON only: {{"workflow": "support|sales|unclear", "category": "<one category or null>", "confidence": "high|medium|low"}}
Categories for support: {support}
Categories for sales: {sales}
Choose "unclear" if the message is not clearly a support problem or a sales enquiry. Do not answer the message."""


def _prompt() -> str:
    fmt = lambda d: "; ".join(f"{k} ({v})" for k, v in d.items())  # noqa: E731
    return _PROMPT.format(support=fmt(CATEGORIES["support"]), sales=fmt(CATEGORIES["sales"]))


def parse_label(raw: str) -> dict | None:
    """Validate the model's reply. Returns {workflow, category, confidence} or None when it must be ignored."""
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    m = re.search(r"\{.*?\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    wf, cat, conf = str(d.get("workflow", "")).lower(), d.get("category"), str(d.get("confidence", "")).lower()
    if wf not in VALID_WORKFLOWS or conf not in ("high", "medium"):
        return None
    cat = str(cat).lower() if cat else None
    if cat not in CATEGORIES[wf]:
        return None
    return {"workflow": wf, "category": cat, "confidence": conf}


_CACHE: dict[str, dict | None] = {}


def suggest(message: str, *, client=None) -> dict | None:
    """One model call (cached per message, so the Router and the Planner asking about the same text cost one call).
    Never raises: any failure returns None and the rules' answer stands."""
    if client is None and message in _CACHE:
        return _CACHE[message]
    out = _suggest(message, client)
    if client is None:
        if len(_CACHE) > 256:
            _CACHE.clear()
        _CACHE[message] = out
    return out


def _suggest(message: str, client) -> dict | None:
    try:
        c = client or openai_client()
        resp = c.chat.completions.create(model=settings.chat_model, temperature=0, max_tokens=120,
                                         messages=[{"role": "system", "content": _prompt()},
                                                   {"role": "user", "content": message[:1500]}])
        return parse_label(resp.choices[0].message.content)
    except Exception:  # noqa: BLE001
        return None


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("model_fallback") or {}).get("enabled"))
