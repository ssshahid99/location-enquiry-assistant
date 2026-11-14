"""Router: send each message to the cheapest component that can handle it safely.

  refused  the scope/ethics guard says this is not something the system processes
  rag      simple, answerable from documentation (also general questions, prices, how-tos, complete support questions)
  agent    complex or case work: incidents, missing information to collect, sales qualification, an already open case
  human    needs a person's judgement (contract/SLA requests, security incidents): a case and handoff are prepared, nothing is drafted for the customer

Rules only (no model call), so routing is instant, free and explainable; every decision carries its reason and signals.
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

from ..generate import guard, meta
from ..safety.escalation import check as escalation_check
from ..tools.pricing_intent import has_volume
from . import planner

_CONTEXT = re.compile(r"\b(?:we|we're|we'd|we'll|our|us|i'm|i am|my)\b", re.I)    # the customer is describing their own situation

ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((ROOT / "config" / "router.yaml").read_text(encoding="utf-8"))


def route(message: str, *, case_open: bool = False) -> dict:
    """-> {route, reason, signals, plan}"""
    cfg = config()
    g = guard.check(message)
    if g["refuse"]:
        return {"route": "refused", "reason": f"guard: {g['category']}", "signals": {"guard": g["category"]}, "plan": None}
    if meta.classify(message):
        return {"route": "rag", "reason": "general question about the system", "signals": {"meta": meta.classify(message)}, "plan": None}
    off = guard.domain_gate(message, None)          # obvious off-topic subjects (weather, recipes, capitals...) never open a case
    if off:
        return {"route": "refused", "reason": "guard: off_topic", "signals": {"guard": "off_topic"}, "plan": None}
    plan = planner.plan(message)
    sig = {"workflow": plan["workflow"], "category": plan["category"], "urgency": plan["urgency"],
           "missing_required": plan["missing_required"], "escalation": plan["escalation"].get("category")}
    esc = plan["escalation"]
    if esc["recommend"] and esc["category"] in cfg["human_judgment_categories"]:
        return {"route": "human", "reason": f"needs human judgement ({esc['category']})", "signals": sig, "plan": plan}
    if case_open:
        return {"route": "agent", "reason": "continuation of an open case", "signals": sig, "plan": plan}
    if not esc["recommend"] and not plan["service"]["providers"] and not guard.has_strong_domain_term(message):
        # no provider and no location-API vocabulary: do not open a case; the answer pipeline's own scope check decides
        return {"route": "rag", "reason": "no location-API signals: the answer pipeline decides scope", "signals": sig, "plan": plan}
    if esc["recommend"]:
        return {"route": "agent", "reason": f"escalation category {esc['category']}: case with approvals", "signals": sig, "plan": plan}
    if plan["workflow"] == "unclear":
        return {"route": "rag", "reason": "no case signals: answer from documentation", "signals": sig, "plan": plan}
    if plan["workflow"] == "support":
        if plan["category"] == "how_to":
            return {"route": "rag", "reason": "how-to question: answer from documentation", "signals": sig, "plan": plan}
        if plan["category"] in cfg["incident_categories"]:
            return {"route": "agent", "reason": f"support incident category {plan['category']}", "signals": sig, "plan": plan}
        if cfg["support_agent_when_missing_required"] and plan["missing_required"]:
            return {"route": "agent", "reason": f"required information missing: {', '.join(plan['missing_required'])}", "signals": sig, "plan": plan}
        return {"route": "rag", "reason": "support question with the needed details: answer from documentation", "signals": sig, "plan": plan}
    if has_volume(message) and (planner.mentions_pricing(message) or plan["category"] == "competitive"):
        # a priced question with a volume (including "A vs B cost for N requests"): the deterministic calculator answers it exactly and cheaply
        return {"route": "rag", "reason": "priced question with a volume: calculator and documentation", "signals": sig, "plan": plan}
    if plan["category"] in cfg["sales_agent_categories"]:
        if not _CONTEXT.search(message):
            return {"route": "rag", "reason": "generic question without customer context: answer from documentation", "signals": sig, "plan": plan}
        return {"route": "agent", "reason": f"sales {plan['category']}: qualification and capability matching", "signals": sig, "plan": plan}
    return {"route": "rag", "reason": "sales pricing / simple question: calculator and documentation", "signals": sig, "plan": plan}
