# GT v2.6 gold rules

> **About this file.** Purpose: the qualification-extraction fixes, rule decisions and label overrides behind the GT v2.6 gold (freeze `eval-freeze-v3`, tag pending).
> Use case: reference for `mapping/map_gt.py`, `mapping/label_overrides.json`, the scorer and reviewers.
> Example: R7 turns "riders in India and Nepal" into `region = "India, Nepal"`.

**Approval:** CF1–CF4, R1–R14, L1 and L2 **APPROVED by Shahid Kamal, 2026-11-16 14:03 IST**. L3 **NOT approved**; GT-LBS-AC-086 is unchanged.
**Base:** `eval-freeze-v2` (22e7973), raw GT v2.5 (unchanged, bound by SHA-256 in `eval/freeze-manifest.json`).
**Principle:** spec §4.3, "stated facts are copied, not invented". Where a rule had an open choice, the conservative option was taken.
**Source text:** `[user]` turns only; a quoted customer message ("Customer wrote: …") counts as user text.
Changes against `eval-freeze-v2`: `docs/gt-v2.6-errata.md` and `eval/v2.6-changes.csv`.

## Confirmed fixes (defects found in the frozen-80 audit)

| ID | Field | Rule | Example |
|---|---|---|---|
| CF1 | monthly_volume | Up to 5 API-phrase tokens from a closed list (provider/product words or a short parenthesised gloss) may sit between the number and the unit. | GT-LBS-DIR-011 "about 6,000,000 route (directions) requests a month" → 6000000 requests_per_month |
| CF2 | region | India also for "for/in an India(-only\|-wide) …" (not when followed by data/residency/hosting/storage) and "as well as India"; otherwise the first country of a closed list (Nepal, Bhutan, Bangladesh, Sri Lanka, Pakistan, Indonesia, Thailand, Singapore, United Arab Emirates, UK) after in/across/for/throughout/within. | GT-LBS-DM-226 "for an India-only app" → India; GT-LBS-AC-210 "good data for Bhutan" → Bhutan |
| CF3 | current_provider | "our <modifier> <provider>" with closed modifiers (self-hosted, own, existing, current, internal, production, paid, on-prem). | GT-LBS-DM-218 "Our self-hosted OSRM /table call" → OSRM |
| CF4 | use_case | No 80-character cap on "We are a/an …". | GT-LBS-AC-234 "…automatic card b" → "…automatic card billing" |

## Rules

### current_provider (schema: one of six canonical providers or null)
- **R1 Possessive use counts.** "our <provider> <request/calls/bill …>" is current use. A product name without a provider name is not aliased. Ex: GT-LBS-DIR-108 "Our Google Directions request" → Google Maps Platform; GT-LBS-DIR-223 "Our Routes API" → null.
- **R2 "<provider> customer" counts.** Ex: GT-LBS-AC-234 "We are a standard self-service Ola Maps customer" → Ola Maps.
- **R3 Existing project counts only with the provider named.** Ex: "Existing Google project" → Google Maps Platform; GT-LBS-DM-026 "…existing project." → null.
- **R4 An incident or error report alone does not set a provider (conservative).** Ex: GT-LBS-DIR-084 "Ola Directions returns 429 for 80% of calls" → null.
- **R5 Explicit first-person usage verbs count:** "our <thing> calls/uses/queries/hits <provider>", "we reverse-geocode/geocode/route/call/query … with <provider>", "<provider> … used by our". A commitment is not a usage verb. Ex: GT-LBS-RG-082 "Our mobile app calls Nominatim reverse" → OSM Nominatim; GT-LBS-DIR-085 "committed to 40M Ola Directions calls" → null.
- **R14 Two or more current providers.** The schema enum allows one value, so keep the provider named next to the record's API; otherwise the earliest cue. A compared or migration-target provider never counts ("migrating from X to Y" → X). Ex: "We use Google for reverse geocoding and we use Mapbox for routing" → Mapbox on a directions record, Google Maps Platform on a reverse_geocode record. Effect on GT v2.5: 0 records (no row states two current providers; 40 rows name 2+ providers, 38 only compare, 2 unclear).

### technical_constraints (optional)
- **R6 Definition.** A user-stated requirement on how the API is called or deployed: "no/without traffic" and a stated request rate (counted wherever stated); a declarative traffic-aware need, our/we self-hosting, on-prem deployment, data residency. Excluded: request sizes (stops, matrix dimensions), storage/caching, language, SLA asks, and options only asked about. Verbatim span; several joined by "; ". Ex: GT-LBS-DIR-020 "no traffic needed"; GT-LBS-RG-212 "40 driver pings a second"; GT-LBS-DIR-221 "…from a self-hosted OSRM?" → null; GT-LBS-DIR-043 "If we self-host OSRM…?" → null.

### region (schema: `string | null`, no array)
- **R7 Several countries** after a location preposition or chained with and/","/as well as are joined alphabetically with ", " in the single string. Ex: GT-LBS-RG-201 "riders in India and Nepal" → "India, Nepal".
- **R8 A city alone sets no region (conservative).** Ex: GT-LBS-DM-085 "for Bengaluru" → null.
- **R9 Data residency is a technical constraint, not a region.** Ex: GT-LBS-AC-089 "India-only data residency" → technical_constraints; region null.
- **R10 Naming the India price list, charges or free cap sets India** (extends the existing "India price list" rule). Ex: GT-LBS-DM-233 "Google Maps Platform India legacy Distance Matrix charges" → India.
- **R11 "global" is not a region (conservative).** Ex: GT-LBS-AC-236 "on the global price card" → null.

### decision_maker (optional)
- **R12** Only a named person/role/team that decides, approves or signs off. A team that wants something is a requester. Ex: GT-LBS-RG-233 "Our legal team wants to store…" → null. Effect on GT v2.5: 0 records.

### use_case (unscored diagnostic, design §6.2)
- **R13** Also "We're a/an X" and "We run a/an X"; the phrase stops before "and expect/need/want/plan/are/use". Ex: GT-LBS-DIR-011 → "Kolkata two-wheeler rental company".

## Label overrides (`mapping/label_overrides.json`, applied at mapping time; raw GT not edited)
- **L1 — APPROVED.** capability_match = "which provider/API can do X / what limits" with no buying context; sales_qualification = discovery, licensing fit or solution design for a stated customer need. GT-LBS-DIR-039 sales_qualification/capability_fit → capability_match/capability_matching (its question matches GT-LBS-DIR-113). Team stays Sales.
- **L2 — APPROVED.** Sales = pre-sale/commercial; Support = a problem with an existing integration. GT-LBS-AC-073 capability_match/capability_question, Sales → troubleshooting/accuracy_question, Support ("We use Mapbox Geocoding v6 … and get no POIs"; the GT owner is already Support L1). Derived: `responsible_role.team` → Support.
- **L3 — NOT APPROVED, unchanged.** GT-LBS-AC-086 keeps api=autocomplete although "Our needs exceed Nominatim's public limits" does not name the API.

Frozen effect of L1+L2: per intent sales_qualification 9, troubleshooting 11, capability_match 10, others 10 (all ≥ 8); team Sales 47 (58.75%) / Support 33 (41.25%), inside 40–60%; buckets and APIs unchanged.
