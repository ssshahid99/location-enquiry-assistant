"""Runtime validation of the 7 copilot outputs against the final JSON Schemas (schemas/, copied from the Track-B repo; Activity 14).

validate_outputs(outputs) -> list of error strings (empty = valid). The same check the Track-B validator applies to the gold records.
"""
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

DIR = Path(__file__).resolve().parents[3] / "schemas"
OUTPUTS = ["response_draft", "clarifying_questions", "qualification_details", "proposed_record_update", "comparison",
           "responsible_role", "next_action"]


@lru_cache(maxsize=1)
def _schemas() -> dict[str, dict]:
    return {p.name.removesuffix(".schema.json"): json.loads(p.read_text(encoding="utf-8")) for p in DIR.glob("*.schema.json")}


@lru_cache(maxsize=1)
def _registry() -> Registry:
    reg = Registry()
    for doc in _schemas().values():
        reg = reg.with_resource(doc["$id"], Resource.from_contents(doc, DRAFT202012))
    return reg


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(_schemas()[name], registry=_registry())


def validate_outputs(outputs: dict) -> list[str]:
    errors = []
    for name in OUTPUTS:
        if name not in outputs:
            errors.append(f"{name}: missing")
            continue
        for e in _validator(name).iter_errors(outputs[name]):
            errors.append(f"{name}: {'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message[:160]}")
    errors += [f"{k}: not an output of the spec" for k in outputs if k not in OUTPUTS]
    return errors
