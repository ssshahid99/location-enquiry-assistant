#!/usr/bin/env python3
"""Validate C1 task-spec artefacts and mapped GT gold outputs.

Reads GT v2.5 in place and never writes to it.

Purpose: check the 8 schemas, map all GT records with ``mapping/map_gt.py``
and test every envelope against the schemas, the human-approval rule,
required qualification fields and the 1,024-token output budget.
Use case: the C1 acceptance check; rerun after any GT, schema or vocabulary
change. It regenerates the gold JSONL files, ``mapping_issues.json``,
``gt-to-schema-mapping.md``, the v2.5 vocab diff and ``validator-report.md``
under the repo root, or under ``--out-dir`` when given.
Example: ``.venv/bin/python3 validator/validate_spec.py --out-dir /tmp/c1``
reports "All records: 532/532 pass" and "80/80 pass"; a copy of
GT-LBS-DIR-002 mutated to ``executed=true`` while ``pending`` is rejected.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mapping.map_gt import (  # noqa: E402
    DEFAULT_GT,
    approx_output_tokens,
    load_gt,
    load_vocab,
    map_all,
    parse_escalation,
    split_csv,
)

SCHEMAS_DIR = REPO / "schemas"
VOCAB_PATH = REPO / "vocab" / "controlled-vocabulary.json"
OUTPUT_SCHEMA_NAMES = [
    "response_draft.schema.json",
    "clarifying_questions.schema.json",
    "qualification_details.schema.json",
    "proposed_record_update.schema.json",
    "comparison.schema.json",
    "responsible_role.schema.json",
    "next_action.schema.json",
]
ENVELOPE_NAME = "envelope.schema.json"
OUTPUT_KEYS = [
    "response_draft",
    "clarifying_questions",
    "qualification_details",
    "proposed_record_update",
    "comparison",
    "responsible_role",
    "next_action",
]


def load_schemas() -> tuple[dict[str, dict], Registry, list[str]]:
    errors: list[str] = []
    schemas: dict[str, dict] = {}
    registry = Registry()
    for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = data
        if not data.get("x-spec-version"):
            errors.append(f"{path.name}: missing x-spec-version")
        if not data.get("$id"):
            errors.append(f"{path.name}: missing $id")
        try:
            Draft202012Validator.check_schema(data)
        except SchemaError as exc:
            errors.append(f"{path.name}: invalid schema: {exc.message}")
        resource = Resource.from_contents(data, default_specification=DRAFT202012)
        registry = registry.with_resource(path.name, resource)
        if data.get("$id"):
            registry = registry.with_resource(data["$id"], resource)
    return schemas, registry, errors


def validator_for(schema: dict, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(schema, registry=registry)


def approval_violations(env: dict) -> list[str]:
    """Reject any executed consequential action that lacks approval."""
    viol = []
    upd = env["outputs"]["proposed_record_update"]
    nxt = env["outputs"]["next_action"]
    if upd.get("executed") is True and upd.get("approval_status") != "approved":
        viol.append("proposed_record_update.executed_without_approval")
    if upd.get("applicable") and upd.get("requires_human_approval") is not True:
        viol.append("proposed_record_update.applicable_without_requires_human_approval")
    if upd.get("executed") is True and not upd.get("applicable"):
        viol.append("proposed_record_update.executed_but_not_applicable")
    if nxt.get("executed") is True and nxt.get("approval_status") != "approved":
        viol.append("next_action.executed_without_approval")
    if nxt.get("action_type") in {"propose_record_update", "propose_escalation"}:
        if nxt.get("requires_human_approval") is not True:
            viol.append("next_action.consequential_without_requires_human_approval")
        if nxt.get("executed") is True and nxt.get("approval_status") != "approved":
            viol.append("next_action.consequential_executed_without_approval")
    return viol


def token_violations(env: dict) -> list[str]:
    draft = env["outputs"]["response_draft"]["draft_text"]
    tokens = env.get("approx_output_tokens")
    computed = approx_output_tokens(draft)
    viol = []
    if tokens != computed:
        viol.append(f"token_count_mismatch:stored={tokens},computed={computed}")
    if computed > env.get("output_token_budget", 1024):
        viol.append(f"output_token_budget_exceeded:{computed}>1024")
    return viol


def required_field_violations(env: dict, vocab: dict) -> list[str]:
    q = env["outputs"]["qualification_details"]
    required = vocab["qualification_required_fields"]
    missing_declared = list(q.get("missing_required_fields") or [])
    actual_missing = [f for f in required if q.get(f) is None]
    viol = []
    if sorted(missing_declared) != sorted(actual_missing):
        viol.append(
            "missing_required_fields_inconsistent:"
            f"declared={missing_declared},actual={actual_missing}"
        )
    expected_complete = len(actual_missing) == 0
    if bool(q.get("complete")) != expected_complete:
        viol.append(f"complete_flag_inconsistent:complete={q.get('complete')}")
    return viol


def vocab_diff(data: pd.DataFrame, vocab: dict) -> dict:
    columns = {
        "intent": "intents",
        "sub_intent": "sub_intents",
        "bucket": "buckets",
        "api": "apis",
        "primary_team": "teams",
    }
    report = {"matches": {}, "merges": {}, "dropped": {}, "new_in_gt": {}, "notes": []}
    for col, key in columns.items():
        gt_vals = set(data[col].dropna().astype(str).unique())
        vocab_vals = set(vocab[key])
        report["matches"][col] = sorted(gt_vals & vocab_vals)
        new = sorted(gt_vals - vocab_vals)
        missing = sorted(vocab_vals - gt_vals)
        if new:
            report["new_in_gt"][col] = new
        if missing:
            report["notes"].append(f"{col}: vocab labels not observed in GT v2.5 Data: {missing}")

    # providers (parsed)
    merges = vocab.get("provider_merges") or {}
    raw_parts = set()
    for raw in data["service_providers"].dropna().astype(str):
        for part in raw.split(","):
            p = part.strip()
            if p:
                raw_parts.add(p)
    vocab_p = set(vocab["providers"])
    report["matches"]["providers"] = sorted(raw_parts & vocab_p)
    merged = sorted(p for p in raw_parts if p in merges)
    report["merges"]["providers"] = {p: merges[p] for p in merged}
    leftover = sorted(p for p in (raw_parts - vocab_p) if p not in merges)
    if leftover:
        report["new_in_gt"]["providers"] = leftover

    # tools
    raw_tools = set()
    for raw in data["expected_tools"].dropna().astype(str):
        for part in raw.split(","):
            p = part.strip()
            if p:
                raw_tools.add(p)
    report["matches"]["tools"] = sorted(raw_tools & set(vocab["tools"]))
    dropped = sorted(raw_tools & set(vocab["tools_dropped"]))
    report["dropped"]["tools"] = dropped
    leftover_t = sorted(raw_tools - set(vocab["tools"]) - set(vocab["tools_dropped"]))
    if leftover_t:
        report["new_in_gt"]["tools"] = leftover_t

    # owners and severities from escalation
    owners, sevs = set(), set()
    for raw in data["escalation"].dropna().astype(str):
        esc = parse_escalation(raw)
        if esc.get("owner"):
            owners.add(esc["owner"])
        if esc.get("severity"):
            sevs.add(esc["severity"])
    report["matches"]["owners"] = sorted(owners & set(vocab["owners"]))
    leftover_o = sorted(owners - set(vocab["owners"]))
    if leftover_o:
        report["new_in_gt"]["owners"] = leftover_o
    report["matches"]["severities"] = sorted(sevs & set(vocab["severities"]))
    leftover_s = sorted(sevs - set(vocab["severities"]))
    if leftover_s:
        report["new_in_gt"]["severities"] = leftover_s

    # sub_intent coverage vs approved map
    gt_subs = set(data["sub_intent"].dropna().astype(str).unique())
    map_subs = set(vocab["sub_intents"])
    if gt_subs - map_subs:
        report["new_in_gt"]["sub_intent_unmapped"] = sorted(gt_subs - map_subs)
    if map_subs - gt_subs:
        report["notes"].append(
            "Approved sub_intent labels not observed in GT v2.5: "
            + ", ".join(sorted(map_subs - gt_subs))
        )
    return report

def diff_count(diff: dict) -> int:
    n = 0
    for mapping in (diff.get("merges") or {}).values():
        n += len(mapping or {})
    for items in (diff.get("dropped") or {}).values():
        n += len(items or [])
    for items in (diff.get("new_in_gt") or {}).values():
        n += len(items or [])
    return n


def render_vocab_diff_md(diff: dict, data: pd.DataFrame, vocab: dict) -> str:
    lines = [
        "# Vocabulary diff against GT v2.5",
        "",
        f"Source of truth: `/workspace/capstone-trackb/data/gt_v2_5.xlsx`, sheet `Data` ({len(data)} records).",
        "Controlled vocabulary: `vocab/controlled-vocabulary.json` version "
        + vocab["version"]
        + ".",
        "",
        "This is a read-only comparison. The GT file was not modified.",
        "",
        f"Vocabulary differences: **{diff_count(diff)}** "
        "(merges still present in GT, dropped labels still present in GT, "
        "or new GT labels not in the vocabulary).",
        "",
        "## Matches (same label in GT and the vocabulary)",
        "",
    ]
    for col, vals in diff["matches"].items():
        lines.append(f"- **{col}** ({len(vals)}): " + (", ".join(vals) if vals else "—"))
    lines += ["", "## Merged (GT label → vocabulary label)", ""]
    if diff["merges"].get("providers"):
        for src, dst in diff["merges"]["providers"].items():
            n = 0
            for raw in data["service_providers"].astype(str):
                parts = [p.strip() for p in raw.split(",")]
                if src in parts:
                    n += 1
            lines.append(f"- `{src}` → `{dst}` on **{n}** records.")
    else:
        lines.append("- None.")
    lines += ["", "## Dropped (present in GT, retired from the copilot vocabulary)", ""]
    if diff["dropped"].get("tools"):
        for t in diff["dropped"]["tools"]:
            n = int(data["expected_tools"].astype(str).str.contains(t, regex=False).sum())
            lines.append(
                f"- `{t}` is dropped as a copilot tool. It remains on **{n}** GT v2.5 "
                "records in `expected_tools`."
            )
    else:
        lines.append("- None.")
    lines += ["", "## New in GT v2.5 and not in the vocabulary", ""]
    if diff["new_in_gt"]:
        for col, vals in diff["new_in_gt"].items():
            lines.append(f"- **{col}**: " + ", ".join(f"`{v}`" for v in vals))
        lines.append("")
        lines.append(
            "No new intent or sub_intent labels were invented or assigned. "
            "Any label listed here is reported, not mapped."
        )
    else:
        lines.append(
            "- None. Every distinct GT v2.5 intent, sub_intent, bucket, API, team, "
            "owner, severity, provider and tool value is accounted for. There are "
            "0 vocabulary differences."
        )
    if diff["notes"]:
        lines += ["", "## Notes", ""]
        for n in diff["notes"]:
            lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines)


def render_report(
    *,
    schema_errors: list[str],
    schemas: dict,
    results: list[dict],
    mapping_issues: list[dict],
    vocab: dict,
    data: pd.DataFrame,
    token_note: str,
    gt_path: Path,
    vocab_diff_result: dict | None = None,
) -> str:
    frozen = [r for r in results if r["frozen_core"]]
    all_n = len(results)
    frozen_n = len(frozen)
    all_pass = sum(1 for r in results if r["ok"])
    frozen_pass = sum(1 for r in frozen if r["ok"])
    all_fail = all_n - all_pass
    frozen_fail = frozen_n - frozen_pass

    reasons = Counter()
    frozen_reasons = Counter()
    for r in results:
        for err in r["errors"]:
            key = err.split(":")[0]
            reasons[key] += 1
            if r["frozen_core"]:
                frozen_reasons[key] += 1

    map_counts = Counter(i["issue"].split(":")[0] for i in mapping_issues)

    tokens = [r["approx_output_tokens"] for r in results]
    f_tokens = [r["approx_output_tokens"] for r in frozen]
    over = [r for r in results if r["approx_output_tokens"] > 1024]

    lines = [
        "# C1 validator report",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"GT source (read only): `{gt_path}`.",
        "Schemas: `schemas/*.schema.json` version 1.0.0.",
        "Vocabulary: `vocab/controlled-vocabulary.json` version "
        + vocab["version"]
        + ".",
        "",
        "## Schema self-check",
        "",
    ]
    if schema_errors:
        lines.append("FAIL. Issues:")
        for e in schema_errors:
            lines.append(f"- {e}")
    else:
        lines.append(
            f"PASS. {len(schemas)} versioned JSON Schemas loaded "
            f"({len(OUTPUT_SCHEMA_NAMES)} outputs + envelope) and each is a valid "
            "JSON Schema draft 2020-12 document with `$id` and `x-spec-version`."
        )
        lines.append("")
        lines.append("Files:")
        for name in OUTPUT_SCHEMA_NAMES + [ENVELOPE_NAME]:
            ver = schemas[name].get("x-spec-version")
            lines.append(f"- `schemas/{name}` — x-spec-version {ver}")

    lines += [
        "",
        f"## Mapping of all {all_n} records",
        "",
        f"Mapped **{all_n}** records from sheet `Data`.",
        f"Mapping-time warnings (non-fatal unless they also fail schema/approval/token checks): "
        f"**{len(mapping_issues)}** on **{len({i['record_id'] for i in mapping_issues})}** records.",
        "",
    ]
    if map_counts:
        lines.append("| Warning | Count |")
        lines.append("|---------|-------|")
        for k, n in map_counts.most_common():
            lines.append(f"| {k} | {n} |")
        lines.append("")
    else:
        lines.append("No mapping-time warnings.")
        lines.append("")

    lines += [
        "## Validation against schemas, approval rule, required fields and token budget",
        "",
        f"- All records: **{all_pass}/{all_n} pass**, {all_fail} fail.",
        f"- Frozen gold expected outputs (`frozen_core=True`): **{frozen_pass}/{frozen_n} pass**, "
        f"{frozen_fail} fail.",
        "",
        "Row 3 acceptance criterion: 100% of frozen gold expected outputs validate with 0 schema errors.",
        "",
    ]
    if frozen_fail == 0 and not schema_errors:
        lines.append(
            f"**PASS** on the frozen set: {frozen_n}/{frozen_n} envelopes and all 7 "
            "nested outputs validate with 0 schema errors. Approval-boundary and "
            "required-field rules also passed on every frozen record."
        )
    else:
        lines.append(
            f"**FAIL** on the frozen set: {frozen_fail} of {frozen_n} records had errors."
        )
    lines += ["", "### Failure reasons (all records)", ""]
    if reasons:
        lines.append("| Reason | All | Frozen |")
        lines.append("|--------|-----|--------|")
        for k, n in reasons.most_common():
            lines.append(f"| {k} | {n} | {frozen_reasons.get(k, 0)} |")
    else:
        lines.append("None.")

    failed = [r for r in results if not r["ok"]]
    if failed:
        lines += ["", "### Failed record ids", ""]
        for r in failed[:50]:
            lines.append(f"- {r['record_id']} (frozen={r['frozen_core']}): " + "; ".join(r["errors"][:4]))
        if len(failed) > 50:
            lines.append(f"- … and {len(failed) - 50} more.")

    def _stats(vals):
        if not vals:
            return "n/a"
        return (
            f"n={len(vals)}, min={min(vals)}, median={sorted(vals)[len(vals)//2]}, "
            f"max={max(vals)}"
        )

    lines += [
        "",
        "## Token budget (APPROVED interim method)",
        "",
        token_note,
        "",
        f"- All mapped drafts: {_stats(tokens)}",
        f"- Frozen drafts: {_stats(f_tokens)}",
        f"- Over 1,024 approx tokens: **{len(over)}**",
        "",
        "## Vocabulary differences",
        "",
        *(() if vocab_diff_result is None else (
            f"- Merges still present in GT: {len((vocab_diff_result.get('merges') or {}).get('providers') or {})}.",
            f"- Dropped labels still present in GT: {sum(len(v or []) for v in (vocab_diff_result.get('dropped') or {}).values())}.",
            f"- New GT labels not in the vocabulary: {sum(len(v or []) for v in (vocab_diff_result.get('new_in_gt') or {}).values())}.",
            f"- Vocabulary differences: **{diff_count(vocab_diff_result)}**.",
            "",
        )),
        "## Approval-boundary rule",
        "",
        "A record fails if `executed` is true on `proposed_record_update` or "
        "`next_action` while `approval_status` is not `approved`, or if a "
        "consequential proposal (`update_record` / `propose_escalation`) is "
        "missing `requires_human_approval=true`. Gold mapped outputs keep every "
        "consequential action at `executed=false` and `approval_status=pending`.",
        "",
    ]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate C1 task-spec artefacts and mapped GT gold outputs."
    )
    parser.add_argument(
        "gt_path",
        nargs="?",
        type=Path,
        default=None,
        help="Path to the GT workbook (default: GT v2.5).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Write reports and generated artefacts here instead of the repo. "
            "The default is the repository root, which is the committed layout."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gt_path = args.gt_path or DEFAULT_GT
    out_dir = args.out_dir.resolve() if args.out_dir else REPO
    vocab = load_vocab(VOCAB_PATH)
    schemas, registry, schema_errors = load_schemas()

    missing = [n for n in OUTPUT_SCHEMA_NAMES + [ENVELOPE_NAME] if n not in schemas]
    for n in missing:
        schema_errors.append(f"missing schema file: {n}")

    data, _cites = load_gt(gt_path)
    envelopes, mapping_issues = map_all(gt_path, vocab)

    env_v = validator_for(schemas[ENVELOPE_NAME], registry) if ENVELOPE_NAME in schemas else None
    out_validators = {
        key: validator_for(schemas[name], registry)
        for key, name in zip(OUTPUT_KEYS, OUTPUT_SCHEMA_NAMES)
        if name in schemas
    }

    results = []
    for env in envelopes:
        errors: list[str] = []
        if env_v:
            for err in env_v.iter_errors(env):
                errors.append("schema.envelope:" + err.message)
        for key, val in out_validators.items():
            for err in val.iter_errors(env["outputs"][key]):
                errors.append(f"schema.{key}:" + err.message)
        errors.extend(approval_violations(env))
        errors.extend(token_violations(env))
        errors.extend(required_field_violations(env, vocab))
        results.append(
            {
                "record_id": env["record_id"],
                "frozen_core": env["frozen_core"],
                "split": env["split"],
                "intent": env["intent"],
                "ok": len(errors) == 0,
                "errors": errors,
                "approx_output_tokens": env["approx_output_tokens"],
            }
        )

    # Write gold outputs (mapped; GT file untouched)
    mapping_dir = out_dir / "mapping"
    vocab_dir = out_dir / "vocab"
    validator_dir = out_dir / "validator"
    mapping_dir.mkdir(parents=True, exist_ok=True)
    vocab_dir.mkdir(parents=True, exist_ok=True)
    validator_dir.mkdir(parents=True, exist_ok=True)
    all_path = mapping_dir / "gold_outputs_all.jsonl"
    frozen_path = mapping_dir / "gold_outputs_frozen.jsonl"
    with all_path.open("w", encoding="utf-8") as fh:
        for env in envelopes:
            fh.write(json.dumps(env, ensure_ascii=False) + "\n")
    with frozen_path.open("w", encoding="utf-8") as fh:
        for env in envelopes:
            if env["frozen_core"]:
                fh.write(json.dumps(env, ensure_ascii=False) + "\n")

    issues_path = mapping_dir / "mapping_issues.json"
    issues_path.write_text(json.dumps(mapping_issues, indent=2, ensure_ascii=False) + "\n")

    # Vocab diff
    diff = vocab_diff(data, vocab)
    diff_md = render_vocab_diff_md(diff, data, vocab)
    vocab_diff_md_path = vocab_dir / "vocab-diff-gt-v2_5.md"
    vocab_diff_json_path = vocab_dir / "vocab-diff-gt-v2_5.json"
    vocab_diff_md_path.write_text(diff_md, encoding="utf-8")
    vocab_diff_json_path.write_text(
        json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    token_note = (
        "Token counts use the **APPROVED** interim method: "
        "`ceil(Unicode character count / 4)` applied to `response_draft.draft_text`. "
        "No Qwen/Qwen3-8B tokenizer, tiktoken, or Hugging Face tokenizer was available "
        "offline, and the hosted inference endpoint was not called. Official token "
        "counts will be measured with the endpoint `usage` field in C10/eval. "
        "The hosted inference endpoint is not called from this activity."
    )


    # Negative test: the approval rule must reject executed actions that lack approval.
    import copy
    approval_neg_test = {"update": None, "escalation": None}
    for env in envelopes:
        if approval_neg_test["update"] is None and env["outputs"]["proposed_record_update"]["applicable"]:
            mutated = copy.deepcopy(env)
            mutated["outputs"]["proposed_record_update"]["executed"] = True
            approval_neg_test["update"] = {
                "record_id": env["record_id"],
                "violations": approval_violations(mutated),
                "control": approval_violations(env),
            }
        if approval_neg_test["escalation"] is None and env["outputs"]["next_action"]["action_type"] == "propose_escalation":
            mutated = copy.deepcopy(env)
            mutated["outputs"]["next_action"]["executed"] = True
            approval_neg_test["escalation"] = {
                "record_id": env["record_id"],
                "violations": approval_violations(mutated),
                "control": approval_violations(env),
            }
        if approval_neg_test["update"] and approval_neg_test["escalation"]:
            break

    report = render_report(
        schema_errors=schema_errors,
        schemas=schemas,
        results=results,
        mapping_issues=mapping_issues,
        vocab=vocab,
        data=data,
        token_note=token_note,
        gt_path=gt_path,
        vocab_diff_result=diff,
    )
    # Append approval negative-test evidence
    upd = approval_neg_test.get("update") or {}
    esc = approval_neg_test.get("escalation") or {}
    report += (
        "\n## Approval-rule negative test\n\n"
        "A copy of a mapped gold record is mutated so `executed=true` while "
        "`approval_status` stays `pending`. The validator must reject it. "
        "The unmodified gold record must still pass.\n\n"
        f"- Update proposal ({upd.get('record_id')}): mutated violations = "
        f"`{upd.get('violations')}`; unmodified control = `{upd.get('control')}`.\n"
        f"- Escalation proposal ({esc.get('record_id')}): mutated violations = "
        f"`{esc.get('violations')}`; unmodified control = `{esc.get('control')}`.\n"
    )
    if not (upd.get("violations") and esc.get("violations") and upd.get("control") == [] and esc.get("control") == []):
        schema_errors.append("approval_negative_test_did_not_fire_as_expected")

    report_path = validator_dir / "validator-report.md"
    report_path.write_text(report, encoding="utf-8")

    # Compact mapping summary markdown
    frozen_envs = [e for e in envelopes if e["frozen_core"]]
    frozen_results = [r for r in results if r["frozen_core"]]
    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(out_dir))
        except ValueError:
            return str(path)

    mapping_md = [
        "# GT v2.5 to schema mapping",
        "",
        "The mapper in `mapping/map_gt.py` reads `/workspace/capstone-trackb/data/gt_v2_5.xlsx` "
        "and builds one envelope per record. The GT file is never modified. The v2.5 workbook "
        "already contains the approved tool re-labels and the canonical provider labels, so "
        "the mapper copies `expected_tools` and `service_providers` as they stand.",
        "",
        "## Field mapping",
        "",
        "| Schema output | GT v2.5 source | Rule |",
        "|---------------|----------------|------|",
        "| `record_id`, `intent`, `sub_intent`, `api`, `team`, `bucket`, `split`, `frozen_core` | same-named Data columns (`team` ← `primary_team`) | copied; must already match the controlled vocabulary; approved label overrides from `mapping/label_overrides.json` are applied first |",
        "| `tools_expected` | `expected_tools` | split on commas; tokens must be in the registered tool list |",
        "| `tools_raw_gt` | `expected_tools` | unmodified tokens, for traceability |",
        "| `response_draft.draft_text` | `reference_answer` | copied verbatim |",
        "| `response_draft.citations` | citations sheet (fallback: `cited_doc_spans`) | one object per span |",
        "| `clarifying_questions` | `gold_clarifying_questions` | one question per bullet line; empty if the cell is blank |",
        "| `qualification_details` | `question` (user turns only) + `api` | conservative regex extraction; `use_case_api.api` is always the record's `api`; missing required fields are computed from the extracted values |",
        "| `proposed_record_update` | `gold_tool_trace` (`update_record` JSON); fallback `reference_answer` | `mode` is `propose`; `executed=false`; `approval_status=pending`; `requires_human_approval=true` |",
        "| `comparison` | `intent`, `bucket`, `service_providers`, citations | applicable for comparison intents/buckets, or pricing/sales records with two or more providers |",
        "| `responsible_role` | `escalation`, `primary_team` | parsed `should_escalate`, `severity`, `owner`, `reason` |",
        "| `next_action` | escalation + tools | `propose_escalation` if escalate; else `propose_record_update` if a gold update exists; else `answer_directly`. Consequential actions stay unexecuted and pending approval |",
        "| `approx_output_tokens` | `reference_answer` | APPROVED interim `ceil(chars/4)`; to be measured with the endpoint `usage` field in C10/eval |",
        "| `self_review_date`, `self_review_evidence` | same-named Data columns | copied; blank or null is allowed until the review happens |",
        "",
        "## Qualification extraction rules (GT v2.6, eval-freeze-v3 pending tag)",
        "",
        "Approved by Shahid 2026-11-16 14:03 IST; full text and examples in `docs/gt-v2.6-rules.md`.",
        "",
        "- Source text: `[user]` turns only; `[assistant]` lines are never mined. Untagged questions are treated as user text; a quoted customer message counts as user text.",
        "- `timeline`: set only for a real time expression after by/before/within/until/in (Q1–Q4, next/this week/month/quarter/year, N days/weeks/months, a month name or year, end of month/quarter/year), a launch/go-live/start/migration phrase followed by such an expression, or a standalone \"next month\"-type phrase. A bare \"in <month/year>\" is not a timeline. Otherwise null.",
        "- `monthly_volume` (CF1): \"N [up to 5 API words] <unit> a month / per month / monthly / each month / /month\" (API words: provider and product names or a short parenthesised gloss, e.g. \"route (directions) requests\", \"Ola Directions calls\"), or \"N a month / per month\"; N accepts commas, decimals, k, M, million, lakh, crore. `requests_per_month` is an integer; `unit` is `<unit>_per_month` (requests, calls, lookups, sessions, elements, searches, queries, transactions) or `per_month`.",
        "- `region`: India after in/across/for/throughout, \"Indian cities/market/price\", \"India price list\", \"for/in an India(-only|-wide) …\" or \"as well as India\" (CF2); else the first of a closed country list (Nepal, Bhutan, Bangladesh, Sri Lanka, Pakistan, Indonesia, Thailand, Singapore, United Arab Emirates, UK) after a location preposition (CF2); else India when the India price list/charges/free cap is named (R10). Several countries are joined alphabetically with \", \" in the single string (R7). A city alone (R8), a data-residency requirement (R9) and \"global\" (R11) set no region.",
        "- `current_provider`: set only with a current-use cue (currently …, we use / we're on …, our <provider> … (R1), our <self-hosted/own/existing …> <provider> (CF3), migrating/switching from …, existing <provider> (R3), we are a <provider> customer (R2), our <thing> calls/uses <provider>, we <geocode/route …> … with <provider>, <provider> … used by our (R5)). Evaluation questions and incident reports alone do not count (R4). One canonical provider (schema enum): with 2+ current providers, the one named next to the record's API, else the earliest (R14). Aliases map to the six canonical names.",
        "- `technical_constraints` (R6): no/without traffic and stated request rates wherever stated; declarative traffic-aware need, our/we self-hosting, on-prem and data residency. Request sizes, storage, language, SLA asks and options only asked about are excluded. Verbatim spans joined by \"; \".",
        "- `decision_maker` (R12): only a named person/role/team that decides, approves or signs off. `budget`: unchanged.",
        "- `use_case_api.use_case` (CF4, R13): \"We are a/an …\", \"We're a/an …\", \"We run a/an …\" (no length cap; stops before \"and expect/need/want …\"), or \"Our delivery/logistics/… …\". Unscored diagnostic.",
        "- Label overrides (L1, L2): `mapping/label_overrides.json` relabels GT-LBS-DIR-039 (capability_match/capability_matching) and GT-LBS-AC-073 (troubleshooting/accuracy_question, Support) at mapping time; the GT file is not edited.",
        "- `missing_required_fields` and `complete` are recomputed from these values.",
        "",
        "## Counts",
        "",
        f"- Records mapped: **{len(envelopes)}** (eval "
        f"{sum(1 for e in envelopes if e['split']=='eval')}, holdout "
        f"{sum(1 for e in envelopes if e['split']=='holdout')}).",
        f"- Frozen mapped: **{len(frozen_envs)}**.",
        f"- Validation pass (all): **{sum(1 for r in results if r['ok'])}/{len(results)}**.",
        f"- Validation pass (frozen): **{sum(1 for r in frozen_results if r['ok'])}/{len(frozen_results)}**.",
        f"- Mapping-time warnings: **{len(mapping_issues)}**.",
        "",
        "## Artefacts",
        "",
        f"- `{_rel(all_path)}`",
        f"- `{_rel(frozen_path)}`",
        f"- `{_rel(issues_path)}`",
        f"- `{_rel(vocab_diff_md_path)}`",
        f"- `{_rel(report_path)}`",
        "- Historical vocabulary diffs kept in the repo: `vocab/vocab-diff-gt-v2_4.md`, `vocab/vocab-diff-gt-v2_4.json`, `vocab/vocab-diff-gt-v2_3.md`, `vocab/vocab-diff-gt-v2_3.json`.",
        "",
    ]
    (mapping_dir / "gt-to-schema-mapping.md").write_text("\n".join(mapping_md) + "\n", encoding="utf-8")

    frozen_fail = sum(1 for r in frozen_results if not r["ok"])
    print(report)
    print(f"Wrote {all_path}")
    print(f"Wrote {frozen_path}")
    print(f"Wrote {report_path}")
    if schema_errors or frozen_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
