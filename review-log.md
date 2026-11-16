# Review log — C1 task specification

> **About this file.** Purpose: record of the reviewer policy, spec review, resolved decisions and gold-record reviews.
> Use case: evidence for acceptance criterion 2 ("Shahid reviews and signs off"); sign-off is still pending.
> Example: the 246 `review_priority=high` records (GT v2.5) need a second self-review recorded in `self_review_date` / `self_review_evidence`.

**Document under review:** `spec/spec.md` (schema version 1.0.0) and the schemas, vocabulary, mapping and validator that accompany it.  
**Reviewer policy:** Shahid reviews and signs off.  
**Sign-off:** pending. The sign-off block in `spec/spec.md` is left unsigned.

## Reviewer policy (amended criterion 2)

Criterion 2 of the row 3 acceptance criteria is amended to: **“Shahid reviews and signs off.”**

The 244 records that were `needs_two_reviewers=true` in v2.2 (45 of them frozen) have `review_priority=high` in v2.3 and v2.4; the other 277 are `standard`. GT v2.5 adds 11 records under the same rule (2 `high`, 9 `standard`), giving 246 `high` and 286 `standard`.

- `high` records require a mandatory second-pass self-review by Shahid, with recorded evidence, in addition to the first pass. That second pass is recorded on the record through `self_review_date` and `self_review_evidence`. Both fields may be blank until the review happens. In GT v2.5 they are blank on all 532 records.
- `standard` records require a single recorded review by Shahid.
- Evidence for a `high` second pass: prices and limits checked against the cited official span; escalation decision, owner and severity checked; policy or licensing claims checked; a short note of what was checked and the outcome.
- `review_status` stays `pending` on a record until the required passes are complete. Unvalidated records do not count as confirmed gold.

No gold-record review pass has been completed in this activity. All 532 v2.5 records remain `review_status=pending`, and the self-review fields remain blank.

## Spec review

| Date (Asia/Calcutta) | Reviewer | Pass | Scope | Decision | Evidence |
|---|---|---|---|---|---|
| 2026-11-11 | Capstone Architect (draft) | n/a — author, not sign-off | C1 spec, schemas, vocabulary, mapping, validator, v2.3 change list | Draft complete; submitted for Shahid’s review and sign-off | `spec/spec.md`; `validator/validator-report.md` (80/80 frozen gold outputs validate, 0 schema errors); original vocab diff was `vocab/vocab-diff-gt-v2_2.md` |
| 2026-11-11 | Capstone Architect (repo update) | n/a — author, not sign-off | Point the C1 artefacts at GT v2.3 after the change list was applied | Mapping, vocabulary diff and validator regenerated against v2.3; change list marked APPLIED and kept as history | `validator/validator-report.md`; `vocab/vocab-diff-gt-v2_3.md`; `gt-change-list-v2_3.md` |
| 2026-11-11 | Capstone Architect (repo update) | n/a — author, not sign-off | Point the C1 artefacts at GT v2.4 and apply Shahid’s approved decisions | Mapping, vocabulary diff and validator regenerated against v2.4; decisions 1–7 recorded as resolved; sign-off still pending | `validator/validator-report.md`; `vocab/vocab-diff-gt-v2_4.md`; `spec/spec.md` section 16 |
| 2026-11-14 | Capstone Architect (repo update) | n/a — author, not sign-off | Sync to GT v2.5 and add the Activity 4 freeze manifest | Mapping (532 records), vocabulary diff and validator regenerated against v2.5 (532/532 and 80/80 pass, 0 vocabulary differences); the frozen-80 bucket imbalance is superseded (every bucket ≥ 5); `eval/freeze-manifest.json` added and tagged `eval-freeze-v1`. Open: Activity 5 reviews pending, cosine dedupe not re-run, full split 429/103, Activity 3 κ not evidenced. Sign-off still pending | `validator/validator-report.md`; `vocab/vocab-diff-gt-v2_5.md`; `eval/freeze-manifest.json`; `spec/spec.md` section 12 |
| 2026-11-14 | Capstone Architect (repo update) | n/a — author, not sign-off | Qualification gold correction and re-freeze (Shahid's decisions of 2026-11-14 23:37 IST: fix the mapper and re-freeze; holdout never used for tuning; the Activity 2 gate is the full qualification F1 with F1 excluding `use_case_api` alongside) | `map_gt.py` extracts from `[user]` turns only, with stricter timeline, monthly_volume and current_provider rules. Against the 128-row review list: 60 of 61 bad timelines → null, and GT-LBS-DIR-046 → "next month" (a real time expression); 44/44 missed volumes filled; 23/23 provider rows resolved. All 532 records: timeline 61, monthly_volume 55, current_provider 10 and missing_required_fields 121 changed. Validator 532/532 and 80/80 pass, 0 vocabulary differences. Tag `eval-freeze-v2` supersedes `eval-freeze-v1`. **Needs Shahid** (provider rule boundary): GT-LBS-AC-234 (frozen; "Ola Maps customer" cue → Ola Maps); GT-LBS-AC-107 ("How should we use Google Places…" → null); GT-LBS-DM-114/115/116 ("Existing Google project" quotes → Google Maps Platform); and 12 records where possessive usage ("our Google Directions request/calls/response…") counts as current use, including frozen GT-LBS-DIR-108 | `review/qualification-diff-v2.csv` (outside the repo); `mapping/gt-to-schema-mapping.md`; `eval/freeze-manifest.json`; `docs/activity-2-interpretation-design.md` §6.4 |
| 2026-11-16 | Knowledge & Validation Engineer (repo update, PR) | n/a — author, not sign-off | GT v2.6 gold rules and re-freeze as `eval-freeze-v3` (Shahid's approval 2026-11-16 14:03 IST: CF1–CF4, R1–R14, L1, L2; L3 not approved) | Frozen-80 audit found 9 confirmed qualification defects (missed volumes DIR-011, DIR-012, DIR-085, AC-236; missed regions DM-201, DM-226, AC-210; missed provider DM-218; truncated use_case AC-234), all fixed in `mapping/map_gt.py`; rule boundaries settled as R1–R14; labels corrected through `mapping/label_overrides.json` (DIR-039 → capability_match, AC-073 → troubleshooting/Support); AC-086 unchanged. 177 field changes on 81 of 532 records (54 on 26 frozen). Validator 532/532 and 80/80 pass, 0 mapping warnings. Activity 4 counts pass (each bucket ≥ 5; APIs 20 each; Sales 47 / Support 33). Raw GT unchanged. **Pending:** Application Engineer review, Shahid's decision on the Activity 4 timing condition (agent code already on master), and the `eval-freeze-v3` tag | `docs/gt-v2.6-rules.md`; `docs/gt-v2.6-errata.md`; `eval/v2.6-changes.csv`; `eval/freeze-manifest.json`; `validator/validator-report.md` |
| 2026-11-16 | Knowledge & Validation Engineer (review nits) | n/a — author, not sign-off | Review nits on the GT v2.6 gold correction, after merging `origin/master` `da6ebb7` (PR #2; no content conflicts) | Application Engineer review approved with nits, now fixed. `validator/validate_spec.py` restored to mode 100755. `_COUNTRY_PREP` and `_REGION_LIST_R7` use `re.I`, matching the India rules. Duplicate `across` removed from the India alternation. A missing `label_overrides.json`, or a stale or broken override whose `from` values do not match, raises `LabelOverrideError` instead of a mapping warning. `eval/freeze-manifest.json` `tag_status`: "Application Engineer review: approved with nits (fixed); tag eval-freeze-v3 not created, pending Shahid decision". `mapping/map_gt.py` sha256 `7011eb90ca66450639969da3cc743e6c05ae23ea3af11ef1c1bfcf2078bcfeb0`. Manifest sha256 `d70526cfba45fe2cd515d5f0a36a8a1f363d898a80255fae09d7b1e9b7b35525` (was `cf073a1ea42e0df917958115a964c8fad264fb89a1c8c482b4e2075624267520`). Frozen gold and `gold_outputs_all.jsonl` unchanged (`87f9993bcfbbb0a3f31e191fe6520fed86a8cb49fcd2a177f57f9b9019f08cbe`, `a65fa071a74a0796fb7230f042b6713f10d0658315da091a97124746b586767e`). Tag `eval-freeze-v3` still not created. | `mapping/map_gt.py`; `eval/freeze-manifest.json`; `validator/validate_spec.py`; `tests/validation/test_label_overrides.py` |
| — | Shahid Kamal | 1 (spec) | C1 task spec and schemas | Pending | |
| — | Shahid Kamal | 2 (self-review of the spec, because the document itself defines pricing, policy and escalation rules) | Same | Pending; to be recorded through `self_review_date` and `self_review_evidence` when it happens | |

The spec-review table is unchanged in structure (no new column was added). Self-review of gold records is recorded on the GT fields, not as an extra column here.

## Open decisions (resolved)

1. **Criterion 2 wording — resolved.** Amended to “Shahid reviews and signs off.” Sign-off stays pending and unsigned.
2. **Token method — resolved.** `ceil(chars/4)` is the approved interim method; official measurement will use the endpoint `usage` field in C10/eval.
3. **Frozen-80 bucket imbalance — resolved as an accepted known limitation (v2.4); superseded by GT v2.5.** In v2.4 `docs` had 0 `verified_live` records and `interpretation` had 1; GT v2.5 swaps 16 frozen members and every bucket now has at least 5.
4. **Bare `Support` owner label — resolved.** Removed from the vocabulary. GT v2.4 uses `Support L2` on `GT-LBS-DIR-222` and `GT-LBS-RG-232`.
5. **Self-review columns — resolved.** `self_review_date` and `self_review_evidence` are in the GT, the envelope, the mapper and the spec. Blank is allowed until the review happens.
6. **Empty tool traces on DIR-042 and DIR-064 — resolved in GT v2.4.** Each now has one `doc_search` step.
7. **DM-093 `api_probe` wording — resolved in GT v2.4.** The reference answer now says “Verify against the cited docs.”

## Gold-record reviews

No first-pass or second-pass gold-record reviews are recorded yet. They begin after Shahid signs off the spec, and they follow the priority rules above. Frozen-core records should be reviewed first.
