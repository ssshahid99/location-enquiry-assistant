# GT v2.5 to schema mapping

The mapper in `mapping/map_gt.py` reads `/workspace/capstone-trackb/data/gt_v2_5.xlsx` and builds one envelope per record. The GT file is never modified. The v2.5 workbook already contains the approved tool re-labels and the canonical provider labels, so the mapper copies `expected_tools` and `service_providers` as they stand.

## Field mapping

| Schema output | GT v2.5 source | Rule |
|---------------|----------------|------|
| `record_id`, `intent`, `sub_intent`, `api`, `team`, `bucket`, `split`, `frozen_core` | same-named Data columns (`team` ← `primary_team`) | copied; must already match the controlled vocabulary; approved label overrides from `mapping/label_overrides.json` are applied first |
| `tools_expected` | `expected_tools` | split on commas; tokens must be in the registered tool list |
| `tools_raw_gt` | `expected_tools` | unmodified tokens, for traceability |
| `response_draft.draft_text` | `reference_answer` | copied verbatim |
| `response_draft.citations` | citations sheet (fallback: `cited_doc_spans`) | one object per span |
| `clarifying_questions` | `gold_clarifying_questions` | one question per bullet line; empty if the cell is blank |
| `qualification_details` | `question` (user turns only) + `api` | conservative regex extraction; `use_case_api.api` is always the record's `api`; missing required fields are computed from the extracted values |
| `proposed_record_update` | `gold_tool_trace` (`update_record` JSON); fallback `reference_answer` | `mode` is `propose`; `executed=false`; `approval_status=pending`; `requires_human_approval=true` |
| `comparison` | `intent`, `bucket`, `service_providers`, citations | applicable for comparison intents/buckets, or pricing/sales records with two or more providers |
| `responsible_role` | `escalation`, `primary_team` | parsed `should_escalate`, `severity`, `owner`, `reason` |
| `next_action` | escalation + tools | `propose_escalation` if escalate; else `propose_record_update` if a gold update exists; else `answer_directly`. Consequential actions stay unexecuted and pending approval |
| `approx_output_tokens` | `reference_answer` | APPROVED interim `ceil(chars/4)`; to be measured with the endpoint `usage` field in C10/eval |
| `self_review_date`, `self_review_evidence` | same-named Data columns | copied; blank or null is allowed until the review happens |

## Qualification extraction rules (GT v2.6, eval-freeze-v3 pending tag)

Approved by Shahid 2026-11-16 14:03 IST; full text and examples in `docs/gt-v2.6-rules.md`.

- Source text: `[user]` turns only; `[assistant]` lines are never mined. Untagged questions are treated as user text; a quoted customer message counts as user text.
- `timeline`: set only for a real time expression after by/before/within/until/in (Q1–Q4, next/this week/month/quarter/year, N days/weeks/months, a month name or year, end of month/quarter/year), a launch/go-live/start/migration phrase followed by such an expression, or a standalone "next month"-type phrase. A bare "in <month/year>" is not a timeline. Otherwise null.
- `monthly_volume` (CF1): "N [up to 5 API words] <unit> a month / per month / monthly / each month / /month" (API words: provider and product names or a short parenthesised gloss, e.g. "route (directions) requests", "Ola Directions calls"), or "N a month / per month"; N accepts commas, decimals, k, M, million, lakh, crore. `requests_per_month` is an integer; `unit` is `<unit>_per_month` (requests, calls, lookups, sessions, elements, searches, queries, transactions) or `per_month`.
- `region`: India after in/across/for/throughout, "Indian cities/market/price", "India price list", "for/in an India(-only|-wide) …" or "as well as India" (CF2); else the first of a closed country list (Nepal, Bhutan, Bangladesh, Sri Lanka, Pakistan, Indonesia, Thailand, Singapore, United Arab Emirates, UK) after a location preposition (CF2); else India when the India price list/charges/free cap is named (R10). Several countries are joined alphabetically with ", " in the single string (R7). A city alone (R8), a data-residency requirement (R9) and "global" (R11) set no region.
- `current_provider`: set only with a current-use cue (currently …, we use / we're on …, our <provider> … (R1), our <self-hosted/own/existing …> <provider> (CF3), migrating/switching from …, existing <provider> (R3), we are a <provider> customer (R2), our <thing> calls/uses <provider>, we <geocode/route …> … with <provider>, <provider> … used by our (R5)). Evaluation questions and incident reports alone do not count (R4). One canonical provider (schema enum): with 2+ current providers, the one named next to the record's API, else the earliest (R14). Aliases map to the six canonical names.
- `technical_constraints` (R6): no/without traffic and stated request rates wherever stated; declarative traffic-aware need, our/we self-hosting, on-prem and data residency. Request sizes, storage, language, SLA asks and options only asked about are excluded. Verbatim spans joined by "; ".
- `decision_maker` (R12): only a named person/role/team that decides, approves or signs off. `budget`: unchanged.
- `use_case_api.use_case` (CF4, R13): "We are a/an …", "We're a/an …", "We run a/an …" (no length cap; stops before "and expect/need/want …"), or "Our delivery/logistics/… …". Unscored diagnostic.
- Label overrides (L1, L2): `mapping/label_overrides.json` relabels GT-LBS-DIR-039 (capability_match/capability_matching) and GT-LBS-AC-073 (troubleshooting/accuracy_question, Support) at mapping time; the GT file is not edited.
- `missing_required_fields` and `complete` are recomputed from these values.

## Counts

- Records mapped: **532** (eval 429, holdout 103).
- Frozen mapped: **80**.
- Validation pass (all): **532/532**.
- Validation pass (frozen): **80/80**.
- Mapping-time warnings: **0**.

## Artefacts

- `mapping/gold_outputs_all.jsonl`
- `mapping/gold_outputs_frozen.jsonl`
- `mapping/mapping_issues.json`
- `vocab/vocab-diff-gt-v2_5.md`
- `validator/validator-report.md`
- Historical vocabulary diffs kept in the repo: `vocab/vocab-diff-gt-v2_4.md`, `vocab/vocab-diff-gt-v2_4.json`, `vocab/vocab-diff-gt-v2_3.md`, `vocab/vocab-diff-gt-v2_3.json`.

