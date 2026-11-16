# GT v2.6 errata (against eval-freeze-v2)

> **About this file.** Purpose: every gold change between `eval-freeze-v2` (22e7973) and GT v2.6 (`eval-freeze-v3`, tag pending).
> Use case: review and audit of the re-freeze; the full row-level table is `eval/v2.6-changes.csv` (record_id, frozen, field, old, new, source).
> Example: GT-LBS-DIR-011 `qualification_details.monthly_volume` null → 6,000,000 requests_per_month (CF1).

Rules: `docs/gt-v2.6-rules.md` (approved by Shahid 2026-11-16 14:03 IST). Raw GT unchanged. Only `qualification_details`, the two derived `comparison.equivalent_requirements` fields (`region`, `monthly_volume_raw`) and, for L1/L2, `intent`, `sub_intent`, `team` and `responsible_role.team` changed.

**Totals:** 177 field changes on 81 of 532 records; 54 field changes on 26 frozen records. R12 and R14 change nothing.

| Source | Field changes | Records | Frozen records |
|---|---|---|---|
| CONFIRMED | 66 | 29 | 9 |
| CONFIRMED (CF2) then R7 | 14 | 7 | 1 |
| L1 | 2 | 1 | 1 |
| L2 | 4 | 1 | 1 |
| R10 | 4 | 2 | 1 |
| R13 | 26 | 26 | 4 |
| R5 | 8 | 4 | 3 |
| R6 | 27 | 27 | 9 |
| R7 | 26 | 13 | 3 |

## Frozen records (all field changes except recomputed missing_required_fields/complete)

| Record | Field | Old | New | Source |
|---|---|---|---|---|
| GT-LBS-AC-073 | intent | capability_match | troubleshooting | L2 |
| GT-LBS-AC-073 | responsible_role.team | Sales | Support | L2 |
| GT-LBS-AC-073 | sub_intent | capability_question | accuracy_question | L2 |
| GT-LBS-AC-073 | team | Sales | Support | L2 |
| GT-LBS-AC-089 | qualification_details.technical_constraints | null | India-only data residency | R6 |
| GT-LBS-AC-210 | comparison.equivalent_requirements.region | null | Bhutan | CONFIRMED |
| GT-LBS-AC-210 | qualification_details.region | null | Bhutan | CONFIRMED |
| GT-LBS-AC-234 | qualification_details.use_case_api.use_case | standard self-service Ola Maps customer using Autocomplete with automatic card b | standard self-service Ola Maps customer using Autocomplete with automatic card b | CONFIRMED |
| GT-LBS-AC-236 | qualification_details.monthly_volume | null | {"raw": "40,000 Google Places Autocomplete Requests per month", "requests_per_mo | CONFIRMED |
| GT-LBS-DIR-011 | comparison.equivalent_requirements.monthly_volume_raw | null | 6,000,000 route (directions) requests a month | CONFIRMED |
| GT-LBS-DIR-011 | qualification_details.monthly_volume | null | {"raw": "6,000,000 route (directions) requests a month", "requests_per_month": 6 | CONFIRMED |
| GT-LBS-DIR-011 | qualification_details.use_case_api.use_case | null | Kolkata two-wheeler rental company | R13 |
| GT-LBS-DIR-012 | comparison.equivalent_requirements.monthly_volume_raw | null | 8,000,000 route (directions) requests a month | CONFIRMED |
| GT-LBS-DIR-012 | qualification_details.monthly_volume | null | {"raw": "8,000,000 route (directions) requests a month", "requests_per_month": 8 | CONFIRMED |
| GT-LBS-DIR-012 | qualification_details.use_case_api.use_case | null | Ahmedabad pharma distributor | R13 |
| GT-LBS-DIR-020 | qualification_details.technical_constraints | null | no traffic needed | R6 |
| GT-LBS-DIR-021 | qualification_details.technical_constraints | null | traffic-aware | R6 |
| GT-LBS-DIR-039 | intent | sales_qualification | capability_match | L1 |
| GT-LBS-DIR-039 | sub_intent | capability_fit | capability_matching | L1 |
| GT-LBS-DIR-043 | qualification_details.technical_constraints | self-host | null | R6 |
| GT-LBS-DIR-085 | qualification_details.monthly_volume | null | {"raw": "40M Ola Directions calls a month", "requests_per_month": 40000000, "uni | CONFIRMED |
| GT-LBS-DIR-201 | comparison.equivalent_requirements.region | India | India, Nepal | R7 |
| GT-LBS-DIR-201 | qualification_details.region | India | India, Nepal | R7 |
| GT-LBS-DIR-201 | qualification_details.use_case_api.use_case | null | bike-taxi app in India | R13 |
| GT-LBS-DIR-221 | qualification_details.technical_constraints | self-hosted | null | R6 |
| GT-LBS-DM-026 | qualification_details.technical_constraints | null | traffic-aware | R6 |
| GT-LBS-DM-117 | qualification_details.technical_constraints | null | no traffic | R6 |
| GT-LBS-DM-118 | qualification_details.technical_constraints | null | no traffic | R6 |
| GT-LBS-DM-201 | comparison.equivalent_requirements.region | null | India, Nepal | CONFIRMED (CF2) then R7 |
| GT-LBS-DM-201 | qualification_details.region | null | India, Nepal | CONFIRMED (CF2) then R7 |
| GT-LBS-DM-201 | qualification_details.use_case_api.use_case | null | logistics firm in Lucknow adding Kathmandu | R13 |
| GT-LBS-DM-218 | qualification_details.current_provider | null | OSRM | CONFIRMED |
| GT-LBS-DM-226 | qualification_details.region | null | India | CONFIRMED |
| GT-LBS-DM-233 | qualification_details.region | null | India | R10 |
| GT-LBS-RG-082 | qualification_details.current_provider | null | OSM Nominatim | R5 |
| GT-LBS-RG-201 | comparison.equivalent_requirements.region | India | India, Nepal | R7 |
| GT-LBS-RG-201 | qualification_details.region | India | India, Nepal | R7 |
| GT-LBS-RG-212 | qualification_details.current_provider | null | Mapbox | R5 |
| GT-LBS-RG-212 | qualification_details.technical_constraints | null | 40 driver pings a second | R6 |
| GT-LBS-RG-225 | comparison.equivalent_requirements.region | India | India, Nepal | R7 |
| GT-LBS-RG-225 | qualification_details.region | India | India, Nepal | R7 |
| GT-LBS-RG-231 | qualification_details.current_provider | null | Mapbox | R5 |

## Non-frozen records by source

- **CONFIRMED** (20): GT-LBS-DIR-010, GT-LBS-DIR-013, GT-LBS-DIR-014, GT-LBS-DIR-079, GT-LBS-DIR-205, GT-LBS-DIR-206, GT-LBS-DIR-208, GT-LBS-DM-202, GT-LBS-DM-203, GT-LBS-DM-204, GT-LBS-DM-205, GT-LBS-DM-206, GT-LBS-DM-207, GT-LBS-DM-208, GT-LBS-DM-210, GT-LBS-DM-211, GT-LBS-DM-212, GT-LBS-DM-229, GT-LBS-RG-206, GT-LBS-RG-229
- **CONFIRMED (CF2) then R7** (6): GT-LBS-DIR-205, GT-LBS-DIR-206, GT-LBS-DM-202, GT-LBS-DM-203, GT-LBS-DM-204, GT-LBS-DM-205
- **R10** (1): GT-LBS-DM-043
- **R13** (22): GT-LBS-AC-083, GT-LBS-DIR-010, GT-LBS-DIR-013, GT-LBS-DIR-014, GT-LBS-DIR-202, GT-LBS-DIR-203, GT-LBS-DIR-204, GT-LBS-DIR-207, GT-LBS-DIR-208, GT-LBS-DM-005, GT-LBS-DM-006, GT-LBS-DM-007, GT-LBS-DM-008, GT-LBS-DM-009, GT-LBS-DM-010, GT-LBS-DM-011, GT-LBS-DM-012, GT-LBS-DM-013, GT-LBS-DM-202, GT-LBS-DM-203, GT-LBS-DM-204, GT-LBS-DM-205
- **R5** (1): GT-LBS-RG-002
- **R6** (18): GT-LBS-DIR-022, GT-LBS-DIR-023, GT-LBS-DIR-024, GT-LBS-DIR-028, GT-LBS-DIR-029, GT-LBS-DIR-030, GT-LBS-DM-028, GT-LBS-DM-114, GT-LBS-DM-115, GT-LBS-DM-116, GT-LBS-DM-119, GT-LBS-DM-124, GT-LBS-DM-206, GT-LBS-DM-207, GT-LBS-DM-208, GT-LBS-DM-211, GT-LBS-DM-212, GT-LBS-RG-229
- **R7** (10): GT-LBS-AC-208, GT-LBS-AC-209, GT-LBS-DIR-202, GT-LBS-DIR-203, GT-LBS-DIR-204, GT-LBS-RG-202, GT-LBS-RG-203, GT-LBS-RG-204, GT-LBS-RG-210, GT-LBS-RG-211

`missing_required_fields` is recomputed wherever a required slot changed; `complete` stays false on every record that changed.
