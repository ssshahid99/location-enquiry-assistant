"""Rule-based escalation. The system never contacts anyone itself: an escalation becomes a *proposed action*
that a human must approve (project rule: human approval is mandatory for escalations and external sends)."""
import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _rules() -> list[dict]:
    raw = yaml.safe_load((ROOT / "config" / "escalation_rules.yaml").read_text(encoding="utf-8"))["rules"]
    out = []
    for r in raw:
        out.append({**r, "_any": [re.compile(p, re.I) for p in r["any"]],
                    "_and": [re.compile(p, re.I) for p in r.get("and_any", [])],
                    "_not": [re.compile(p, re.I) for p in r.get("not_any", [])]})
    return out


def check(query: str) -> dict:
    """-> {escalate, category, owner, collect, matched}"""
    for r in _rules():
        hit = next((p.pattern for p in r["_any"] if p.search(query)), None)
        if hit and any(p.search(query) for p in r["_not"]):
            continue                       # explicit negation, e.g. "it's not an outage for us"
        if hit and (not r["_and"] or any(p.search(query) for p in r["_and"])):
            return {"escalate": True, "category": r["category"], "owner": r["owner"],
                    "collect": r["collect"], "matched": hit}
    return {"escalate": False, "category": None, "owner": None, "collect": [], "matched": None}
