"""Response drafter (Activity 14): turns what the route produced into the 7 schema-valid outputs of the spec plus the rendered text.

Mostly deterministic: the answer text comes from the route (RAG pipeline / LLM / escalation handoff); everything else is built by code from
the conversation state, the calculator result, the escalation policy SC-02 and the tool results. The result is validated by chat/schemas.py.
"""
import re
from functools import lru_cache

import yaml

from ..agent import planner as case_rules
from ..retrieve.query_analyzer import detect
from ..safety import escalation_policy
from . import schemas

_API = {"Directions": "directions", "Distance Matrix": "distance_matrix", "Autocomplete": "autocomplete", "Reverse Geocode": "reverse_geocode"}
CANON = {"Google Maps Platform", "Ola Maps", "Mapbox", "OSM Nominatim", "OSRM", "Mappls (MapmyIndia)"}      # HERE is not in the spec's vocabulary
_CURRENT = re.compile(r"\b(?:currently|right now|today|already|at the moment)\b.{0,25}\b(?:us(?:e|ing)|on|with|run(?:ning)? on)\b|"
                      r"\b(?:switch(?:ing)?|migrat\w+|mov(?:e|ing)|leav(?:e|ing))\b.{0,12}\b(?:from|off|away from)\b|\bcurrent provider\b", re.I)
REQUIRED = ["use_case_api", "region", "monthly_volume", "current_provider", "timeline"]
_ASK_ALWAYS_SALES = {"discovery", "capability_match", "licensing"}


@lru_cache(maxsize=1)
def _checklist() -> dict:
    return yaml.safe_load((case_rules.ROOT / "config" / "planner.yaml").read_text(encoding="utf-8"))


def _canon(providers: list[str]) -> list[str]:
    return [p for p in providers if p in CANON]


def _api(message: str, facts: dict) -> str | None:
    d = detect(message)["apis"] or detect(facts.get("api", ""))["apis"]
    return _API.get(d[0]) if d else None


def _current_provider(message: str) -> str | None:
    m = _CURRENT.search(message)
    if not m:
        return None
    window = message[m.start(): m.end() + 60]
    got = _canon(detect(window)["providers"])
    return got[0] if got else None


def qualification(message: str, facts: dict) -> dict:
    api = _api(message, facts)
    vol = facts.get("volume")
    q = {
        "use_case_api": {"api": api, "use_case": facts.get("use_case")} if api else None,
        "region": facts.get("regions"),
        "monthly_volume": {"raw": vol, "requests_per_month": None, "unit": None} if vol else None,
        "current_provider": _current_provider(message) or facts.get("current_provider"),
        "timeline": facts.get("timeline"),
        "budget": facts.get("budget"),
        "technical_constraints": facts.get("constraints"),
        "decision_maker": facts.get("decision_maker"),
    }
    q["missing_required_fields"] = [f for f in REQUIRED if not q[f]]
    q["complete"] = not q["missing_required_fields"]
    return q


def clarifying(workflow: str, category: str | None, facts: dict, asked: dict, route: str) -> dict:
    """Questions for what is still missing: sales discovery only (support questions are answered from the documents). Never re-asks."""
    if workflow != "sales" or category not in _ASK_ALWAYS_SALES or route == "escalation":
        return {"needed": False, "questions": []}
    cfg = _checklist()
    limit, max_times = cfg["max_questions"], cfg["max_times_asked"]
    qs = []
    for it in sorted(cfg["sales"], key=lambda i: not i["required"]):
        if it["key"] in facts or asked.get(it["key"], {}).get("answered"):
            continue
        if it["key"] in asked and max_times <= 1:
            continue
        if not it["required"] and len(qs) >= 2:
            continue
        qs.append({"text": it["question"], "field": it["key"]})
        if len(qs) >= limit:
            break
    return {"needed": bool(qs), "questions": qs}


def comparison(message: str, route: str, calc: dict | None, citations: list[dict], facts: dict) -> dict:
    d = detect(message)
    provs = _canon(d["providers"])
    cmp_words = re.search(r"\b(?:compare|comparison|vs\.?|versus|cheaper|cheapest|difference|better|which (?:api|provider)s?)\b", message, re.I)
    priced = _canon(list(dict.fromkeys(r.get("provider") for r in (calc or {}).get("results", []))))
    provs = list(dict.fromkeys(provs + priced))
    if route != "rag" or not (len(provs) >= 2 or (cmp_words and provs)):
        return {"applicable": False, "providers": [], "dimensions": [], "equivalent_requirements": None, "assumptions": [],
                "source_urls": [], "notes": None}
    dims = []
    if (calc or {}).get("results") or case_rules.mentions_pricing(message):
        dims.append("pricing")
    if re.search(r"\b(?:support|capabilit|feature|mode|profile|parameter)", message, re.I):
        dims.append("capabilities")
    if re.search(r"\b(?:limit|quota|max|waypoints|elements)\b", message, re.I):
        dims.append("limits")
    if re.search(r"\b(?:coverage|region|countr|cities|india)\b", message, re.I):
        dims.append("coverage")
    return {"applicable": True, "providers": provs, "dimensions": dims or ["capabilities"],
            "equivalent_requirements": {"api": _api(message, facts), "region": facts.get("regions"), "monthly_volume_raw": facts.get("volume")},
            "assumptions": list((calc or {}).get("assumptions", [])),
            "source_urls": list(dict.fromkeys(c["doc_url"] for c in citations)), "notes": None}


_RECOMMEND = re.compile(r"\brecommend|\bwhich (?:\w+ ){0,3}(?:api|apis|provider|providers)\b.{0,30}\b(?:fit|suit|best|right)\b|options and|"
                        r"what would .{0,20}cost", re.I)


def account_owned(message: str, workflow: str, category: str | None) -> bool:
    """Who owns a NON-escalating sales message. Ground-truth pattern (working pool): the account team owns customer conversations that need
    selling work (a multi-turn exchange, discovery / licensing / contract, a request for a recommendation); a plain documented lookup of a
    price, capability or limit stays with Support L1."""
    if workflow != "sales":
        return False
    return message.lstrip().startswith("[user]") or category in ("discovery", "licensing", "contract") or bool(_RECOMMEND.search(message))


def role(message: str, route: str, esc_category: str | None, workflow: str, category: str | None = None) -> dict:
    a = escalation_policy.assess(message, esc_category if route == "escalation" else None, workflow if workflow in ("sales", "support") else "support")
    if route != "escalation":
        a = {**a, "owner": "Account team (Sales)" if account_owned(message, workflow, category) else "Support L1"}
    team = "Sales" if (workflow == "sales" or a["owner"] == "Account team (Sales)") else "Support"
    why = (f"Escalation: {esc_category.replace('_', ' ')}; policy {escalation_policy.version().split(' ')[0]} assigns {a['severity']} and {a['owner']}."
           if route == "escalation" else
           f"No escalation needed; {a['owner']} normally owns this {workflow if workflow in ('sales', 'support') else 'support'} question ({a['basis']}).")
    return {"owner": a["owner"], "team": team, "should_escalate": route == "escalation", "severity": a["severity"], "reason": why}


def record_fields(message: str, cls: dict, rl: dict) -> dict:
    d = detect(message)
    f = {"category": cls.get("category") or "how_to", "owner": rl["owner"], "severity": rl["severity"]}
    api = _API.get(d["apis"][0]) if d["apis"] else None
    if api:
        f["api"] = api
    if d["providers"]:
        f["providers"] = ", ".join(d["providers"])
    if cls.get("workflow") == "sales":
        f["stage"] = "qualification"
    else:
        f["status"] = "solution_proposed"
    return f


def _span(chunk_text: str, claim: str) -> str:
    t = re.sub(r"\s+", " ", chunk_text or "").strip()
    return t[:240] if t else "(cited passage)"


def response_draft(text: str, citations: list[dict], chunk_text: dict[str, str], *, refused: bool, refusal_reason: str | None) -> dict:
    cites = []
    for c in citations:
        marker = f"[{c['n']}]"
        claim = next((s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text) if marker in s), "")
        cites.append({"url": c["doc_url"], "span": _span(chunk_text.get(c["chunk_id"], ""), claim),
                      "supports_claim": re.sub(r"\[\d+\]", "", claim).strip()[:300] or "Source used for this answer",
                      "quote_or_summary": "summary"})
    return {"draft_text": text or "(no answer)", "citations": cites, "is_refusal": refused,
            "refusal": ({"policy_id": "guard", "reason": refusal_reason or "out of scope or unsafe", "unsafe_or_out_of_scope": True} if refused else None)}


def next_action(route: str, *, refused: bool, clar_needed: bool, role_: dict, esc_summary: str, record: dict | None) -> dict:
    if refused:
        return {"action_type": "refuse", "description": "Refused by the scope and ethics guard; nothing was retrieved or generated.",
                "requires_human_approval": False, "approval_status": "not_applicable", "executed": False, "escalation": None}
    if route == "escalation":
        return {"action_type": "propose_escalation", "description": f"Hand off to {role_['owner']} ({role_['severity']}); waits for human approval.",
                "requires_human_approval": True, "approval_status": "pending", "executed": False,
                "escalation": {"severity": role_["severity"], "owner": role_["owner"], "summary": esc_summary[:400]}}
    if record:
        return {"action_type": "propose_record_update", "description": f"Update {record['record_id']} (field-level diff); waits for human approval.",
                "requires_human_approval": True, "approval_status": "pending", "executed": False, "escalation": None}
    if clar_needed:
        return {"action_type": "ask_clarifying_questions", "description": "Ask the customer for the missing details.",
                "requires_human_approval": False, "approval_status": "not_applicable", "executed": False, "escalation": None}
    return {"action_type": "answer_directly", "description": "Answer the customer directly.", "requires_human_approval": False,
            "approval_status": "not_applicable", "executed": False, "escalation": None}


def record_update(record: dict | None) -> dict:
    if not record:
        return {"applicable": False, "requires_human_approval": False, "approval_status": "not_applicable", "executed": False, "proposal": None}
    return {"applicable": True, "requires_human_approval": True, "approval_status": "pending", "executed": False,
            "proposal": {"record_id": record["record_id"], "mode": "propose", "fields": record["fields"]}}


def build(*, message: str, route: str, cls: dict, facts: dict, asked: dict, text: str, citations: list[dict], chunk_text: dict[str, str],
          calc: dict | None, esc_category: str | None, refused: bool, refusal_reason: str | None, record: dict | None, esc_summary: str = "",
          clar: dict | None = None) -> dict:
    rl = role(message, route, esc_category, cls.get("workflow", "support"), cls.get("category"))
    clar = clar if clar is not None else clarifying(cls.get("workflow", "support"), cls.get("category"), facts, asked, route)
    outputs = {
        "response_draft": response_draft(text, citations, chunk_text, refused=refused, refusal_reason=refusal_reason),
        "clarifying_questions": clar,
        "qualification_details": qualification(message, facts),
        "proposed_record_update": record_update(record),
        "comparison": comparison(message, route, calc, citations, facts),
        "responsible_role": rl,
        "next_action": next_action(route, refused=refused, clar_needed=clar["needed"], role_=rl, esc_summary=esc_summary or text, record=record),
    }
    return outputs


def validate(outputs: dict) -> list[str]:
    return schemas.validate_outputs(outputs)
