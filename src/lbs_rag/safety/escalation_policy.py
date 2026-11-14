"""Escalation policy SC-02: severity and owning team for an escalation category (config/escalation_policy.yaml).

The rules in escalation.py decide WHETHER and WHY to escalate (category + evidence to collect); this module adds HOW URGENT (SEV1-4) and
WHICH TEAM owns it, from the approved policy. Pure and deterministic; nothing is sent or applied (a human still approves every escalation).
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

_CFG = Path(__file__).resolve().parents[3] / "config" / "escalation_policy.yaml"


@lru_cache(maxsize=1)
def policy() -> dict:
    return yaml.safe_load(_CFG.read_text(encoding="utf-8"))


def version() -> str:
    return policy()["policy_version"]


def assess(message: str, category: str | None, workflow: str | None = None) -> dict:
    """-> {severity, owner, policy_version, basis}. `category` is an escalation category (production_incident, security_incident, ...); for an
    ordinary (non-escalating) case pass category=None and the workflow ("support" | "sales")."""
    p = policy()
    if category in p["categories"]:
        spec = p["categories"][category]
        for rule in spec.get("rules", []):
            if re.search(rule["when"], message, re.I):
                out = dict(rule["then"])
                if rule.get("upgrade_when") and re.search(rule["upgrade_when"], message, re.I):
                    out["severity"] = rule["upgrade_to"]
                return {**out, "policy_version": version(), "basis": f"{category}: rule /{rule['when'][:40]}/"}
        return {**spec["default"], "policy_version": version(), "basis": f"{category}: default"}
    base = p["non_escalating"].get("sales" if workflow == "sales" else "support")
    return {**base, "policy_version": version(), "basis": f"no escalation ({workflow or 'support'})"}
