# Interpretation validation cross-check

Read-only comparison of the 532 committed GT v2.5 gold envelopes
(`mapping/gold_outputs_all.jsonl`, sha256 `a65fa071a74a0796fb7230f042b6713f10d0658315da091a97124746b586767e`).
Holdout rows were read and not used to change rules, schemas, or tests.
No provider API was called. The dataset validator and the gold files
were not modified.

Dataset side: `validator.validate_spec.load_schemas`, `validator_for`,
and `required_field_violations` on `outputs.qualification_details`.
`validate_spec.py` was not executed as a program: that entry point
rewrites gold outputs, the vocabulary diff, and the validator report.

New side: `copilot.validation.rules.validate_qualification`, plus
`use_case_api.api` compared with the envelope `api` (the check
`validate_interpretation_output` reports as `API_MISMATCH`).
The dataset validator does not have that rule; a record is a
difference on this field only when the new check fails.

Schema errors on both sides are formatted by the same pointer
function. A schema difference is either a different pass/fail or
a different error list. `missing_required_fields` is order-sensitive
in the new check and sorted in the dataset check. `complete` must
be the boolean `True` or `False` in the new check; the dataset
check uses `bool(complete)`.

## Match count

**532/532** records agree on every compared field.

Dataset qualification checks passed **532/532**.
Interpretation qualification checks passed **532/532** (schema, missing-field list, complete flag, and `use_case_api.api`).

| Field | Records in agreement |
| --- | --- |
| `schema` | 532/532 |
| `missing_required_fields` | 532/532 |
| `complete` | 532/532 |
| `use_case_api.api` | 532/532 |

Splits read: {"eval": 429, "holdout": 103}.

## Differences

None.

## Generated QualificationDetails

Pydantic `QualificationDetails.model_validate` accepted **532/532** qualification objects.

No model failures.
