# C1 validator report

Generated: 2026-11-16T14:09:11+05:30
GT source (read only): `/workspace/capstone-trackb/data/gt_v2_5.xlsx`.
Schemas: `schemas/*.schema.json` version 1.0.0.
Vocabulary: `vocab/controlled-vocabulary.json` version 1.0.0.

## Schema self-check

PASS. 8 versioned JSON Schemas loaded (7 outputs + envelope) and each is a valid JSON Schema draft 2020-12 document with `$id` and `x-spec-version`.

Files:
- `schemas/response_draft.schema.json` — x-spec-version 1.0.0
- `schemas/clarifying_questions.schema.json` — x-spec-version 1.0.0
- `schemas/qualification_details.schema.json` — x-spec-version 1.0.0
- `schemas/proposed_record_update.schema.json` — x-spec-version 1.0.0
- `schemas/comparison.schema.json` — x-spec-version 1.0.0
- `schemas/responsible_role.schema.json` — x-spec-version 1.0.0
- `schemas/next_action.schema.json` — x-spec-version 1.0.0
- `schemas/envelope.schema.json` — x-spec-version 1.0.0

## Mapping of all 532 records

Mapped **532** records from sheet `Data`.
Mapping-time warnings (non-fatal unless they also fail schema/approval/token checks): **0** on **0** records.

No mapping-time warnings.

## Validation against schemas, approval rule, required fields and token budget

- All records: **532/532 pass**, 0 fail.
- Frozen gold expected outputs (`frozen_core=True`): **80/80 pass**, 0 fail.

Row 3 acceptance criterion: 100% of frozen gold expected outputs validate with 0 schema errors.

**PASS** on the frozen set: 80/80 envelopes and all 7 nested outputs validate with 0 schema errors. Approval-boundary and required-field rules also passed on every frozen record.

### Failure reasons (all records)

None.

## Token budget (APPROVED interim method)

Token counts use the **APPROVED** interim method: `ceil(Unicode character count / 4)` applied to `response_draft.draft_text`. No Qwen/Qwen3-8B tokenizer, tiktoken, or Hugging Face tokenizer was available offline, and the hosted inference endpoint was not called. Official token counts will be measured with the endpoint `usage` field in C10/eval. The hosted inference endpoint is not called from this activity.

- All mapped drafts: n=532, min=15, median=89, max=408
- Frozen drafts: n=80, min=20, median=117, max=408
- Over 1,024 approx tokens: **0**

## Vocabulary differences

- Merges still present in GT: 0.
- Dropped labels still present in GT: 0.
- New GT labels not in the vocabulary: 0.
- Vocabulary differences: **0**.

## Approval-boundary rule

A record fails if `executed` is true on `proposed_record_update` or `next_action` while `approval_status` is not `approved`, or if a consequential proposal (`update_record` / `propose_escalation`) is missing `requires_human_approval=true`. Gold mapped outputs keep every consequential action at `executed=false` and `approval_status=pending`.


## Approval-rule negative test

A copy of a mapped gold record is mutated so `executed=true` while `approval_status` stays `pending`. The validator must reject it. The unmodified gold record must still pass.

- Update proposal (GT-LBS-DIR-002): mutated violations = `['proposed_record_update.executed_without_approval']`; unmodified control = `[]`.
- Escalation proposal (GT-LBS-DIR-083): mutated violations = `['next_action.executed_without_approval', 'next_action.consequential_executed_without_approval']`; unmodified control = `[]`.
