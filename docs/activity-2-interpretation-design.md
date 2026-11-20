# Activity 2 — Interpretation step: design contract

> **About this file.** Purpose: the design contract for the Activity 2 interpretation step (query + history → intent, API, team, qualification, missing fields).
> Use case: the implementers build and score against this; it is design only and adds no code.
> Example: for `GT-LBS-DIR-011` the step must return `intent=pricing_comparison`, `api=directions`, `team=Sales`, `region="India"`.

**Status:** draft for Shahid's review; decisions in §6.2, §6.4 and §7 approved 2026-11-14 23:37 IST · **Date:** 2026-11-14 (IST) · **Frozen gold:** tag `eval-freeze-v2` (GT v2.5; supersedes `eval-freeze-v1` at `3b48db9`)
**Author:** Capstone Architect · **Decisions in force (Shahid, 2026-11-14 23:21 IST):** the VM07 Qwen3-8B endpoint is approved for Activity 2 development and scoring by the implementers; code lives in this repo; hybrid design (model output + deterministic rule checks).

Activity 2 text (activities file): *"Build the interpretation step. From the query and history it outputs intent, API, team and a filled qualification object, and it lists which required fields are missing."*
Acceptance: *"Run on the frozen gold set (60–100 queries) against gold labels. Pass: intent accuracy ≥90%, API accuracy ≥95%, qualification field-level F1 ≥0.85, and missing-field recall ≥0.85."*

---

## 1. Purpose and scope

The interpretation step is one plain Python function, `interpret(InterpretationInput) -> InterpretationOutput`. It becomes the Planner's first node later (LangGraph is pending and not needed here). It:

- classifies the turn into one of the 8 scored intents (plus a `sub_intent` detail label), one of the 4 APIs and one of the 2 teams;
- fills the qualification object defined by `schemas/qualification_details.schema.json`;
- computes `missing_required_fields` in code, treating facts already given in history or known state as present.

**Out of scope:** retrieval and citations (`doc_search`), every tool call (`calculator`, `update_record`, `escalate_ticket`), drafting the response, clarifying-question wording, comparison, owner/escalation routing, next action, and the conversation state store itself (Activity 13; this step only reads `known_facts`). The GT `context` column holds cited doc snippets for the reference answer, so it must **not** be passed to this step.

## 2. Interface contract

Pydantic v2 models live in `copilot/interpret/models.py`. `QualificationDetails` is generated from the JSON Schema (the schemas stay the source of truth). The two wrapper models below are new. They are specified here only and are **not** added to `schemas/` yet.

### 2.1 Input

```python
class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str                                  # minLength 1

class KnownFact(BaseModel):
    field: Literal["use_case_api", "region", "monthly_volume", "current_provider",
                   "timeline", "budget", "technical_constraints", "decision_maker"]
    value: Any                                 # same type as the qualification property
    source: Literal["state_store", "history"]

class InterpretationInput(BaseModel):
    query: str                                 # latest user turn, minLength 1
    history: list[Turn] = []                   # oldest first; may be empty
    known_facts: list[KnownFact] = []          # from the state store; may be empty
    record_id: str | None = None               # set only when scoring
```

GT adapter: 52 of the 532 GT `question` cells (5 frozen) embed history as `[user] …` / `[assistant] …` lines. `copilot/interpret/gt_adapter.py::from_gt_question(text)` splits them into turns: the last `[user]` turn becomes `query` and the rest become `history`. Plain questions become `query` with empty history. For scoring, `known_facts` is always empty because the GT has no state-store column.

### 2.2 Output (`interpretation_output` v0.2.0)

v0.2.0 makes `intent`, `api` and `team` nullable. There is no `unknown` vocabulary value. `abstain_reason` stays an enum and adds `no_rule_match`.

```json
{
  "intent": "pricing_comparison",          // vocab.intents (8), or null when abstain=true
  "sub_intent": "pricing_comparison",      // enum: vocab.sub_intents (31) or null; must be in intent_to_sub_intents[intent]
  "api": "directions",                     // vocab.apis (4), or null when abstain=true
  "team": "Sales",                         // Sales | Support, or null when abstain=true
  "qualification_details": { ... },        // validates against schemas/qualification_details.schema.json exactly
  "missing_required_fields": ["current_provider", "timeline"],  // equal to qualification_details.missing_required_fields
  "field_provenance": {"region": "rule", "monthly_volume": "model", "timeline": "history"},
  "confidence": {"intent": 0.0-1.0 | null, "api": ..., "team": ...},
  "abstain": false,                        // true when the output is rules-only fallback or unresolvable
  "abstain_reason": null,                  // model_unavailable | invalid_after_repair | budget_exceeded | no_rule_match | null
  "trace": {"model": "Qwen/Qwen3-8B", "prompt_version": "interp-v1", "approx_input_tokens": 0,
            "approx_output_tokens": 0, "latency_ms": 0, "repair_attempts": 0, "model_calls": 0,
            "rule_model_disagreements": ["api"]}
}
```

Contract rules:
- `qualification_details` has all 10 required keys of the schema. `use_case_api.api` equals the top-level `api`, including when both are null. A null `api` requires `use_case_api` null; that null is a missing required field, so `complete` is false.
- When `abstain` is false, `intent`, `api` and `team` are non-null vocabulary values.
- When `abstain` is true, each of `intent`, `api` and `team` may be null. `abstain_reason` is required and is one of the four reason strings (non-empty; whitespace-only is invalid). `no_rule_match` is the reason when no rule matched. `abstain=false` requires `abstain_reason` null; the key stays required, so omitting it is invalid.
- `missing_required_fields` = the required fields (`use_case_api, region, monthly_volume, current_provider, timeline`) whose value is null after the history and known-facts merge, in vocabulary order. `complete = (len(missing) == 0)`. Both are computed by code only.
- `confidence` is the model's self-reported value, or null. It is diagnostic only and never used for scoring. `abstain=true` outputs are still scored (no free passes).
- Token counts use `ceil(chars/4)` (`mapping/map_gt.py::approx_output_tokens`) until Activity 8 or Activity 20 starts.

## 3. Pipeline

```
input ─► (1) rule pre-extraction ─► (2) model call ─► (3) post-check ─┬─ valid ─► output
                     │                                                ├─ invalid ─► (4) one repair call ─► (3) again
                     └────────────────────────────────────────────────┴─ still invalid / no model ─► (5) rules-only + abstain
```

1. **Deterministic pre-extraction** (`copilot/interpret/rules.py`). Runs on **user turns only**, never assistant turns. It reuses `mapping/map_gt.py` patterns as the starting point:
   - volumes (`VOLUME_PATTERNS`, `_parse_volume_number`);
   - region (India patterns);
   - provider names (`PROVIDER_ALIASES` onto the 6 canonical names);
   - timelines (`by|before|go-live|needed by|deadline …`, `Q[1-4] YYYY`, `this/next quarter`), with the guards from §6.4;
   - budget, technical constraints (`self-host`, `on-prem`, `N requests per second`, `without traffic`);
   - API keyword hits (directions/routing, distance matrix, autocomplete, reverse geocod*).

   Each hit records its matched span.
2. **Model call** through the thin client (`copilot/llm/client.py`):
   - VM07 base URL fixed by config, fails closed if missing; `OPENAI_*` env vars are ignored; the openai SDK is pinned;
   - `model="Qwen/Qwen3-8B"`, `temperature=0`, `max_tokens` ≤1024 (this step requests 512), thinking disabled (§4);
   - the prompt includes the rule hits as hints. Few-shot examples come only from the allowed pool (§7).
3. **Post-check** (`copilot/interpret/postcheck.py`, deterministic, no model):
   - strip any `<think>…</think>` and parse the first JSON object;
   - enforce the vocabulary: case-folded exact match to `intents`, `sub_intents`, `apis`, `teams`, and `providers` via `PROVIDER_ALIASES`. Anything else is invalid; nothing is guessed;
   - `sub_intent` must belong to `intent_to_sub_intents[intent]`, otherwise it is set to null and logged (sub_intent is not scored);
   - **grounding:** every non-null model qualification value must carry an `evidence` substring that occurs (case-insensitive, whitespace-collapsed) in a user turn or in `known_facts`. Ungrounded values are dropped to null;
   - merge history and known facts (§5), then compute `missing_required_fields` and `complete` in code;
   - validate `qualification_details` with the same `Draft202012Validator` and schema files as `validator/validate_spec.py`. Shared rule functions (e.g. required-field consistency) move to one importable module used by both. `validate_spec.py`'s behaviour and outputs must not change.
4. **Repair:** at most **one** extra model call. It sends the original prompt plus the specific validation errors and asks for corrected JSON only. Then the post-check runs again.
5. **Fallback:** rules-only output.
   - The API comes from the rule hit if exactly one API matched, otherwise `null` with `abstain=true` (v0.2.0: a null API is valid only on that abstain output, with `use_case_api` null).
   - Intent and team come from a fixed keyword table (`rules.py`, built on the allowed pool only), else abstain.
   - Qualification comes from the rule hits.
   - `abstain=true` with a reason. The step never loops and never exceeds 2 model calls per input.

**Which source wins when rules and the model disagree:**

| Field | Winner | Why |
|---|---|---|
| `intent`, `sub_intent`, `team` | model (if in vocab) | semantic; rules only as fallback |
| `api` | rule, **if exactly one API keyword family matched** in user turns; else model | explicit API names are unambiguous |
| `monthly_volume.requests_per_month`, `current_provider`, `region` | rule (verbatim span, deterministic normalisation) | numbers and names are exact-match tasks |
| `timeline`, `budget`, `technical_constraints`, `decision_maker`, `use_case_api.use_case` | model, if grounded; else rule | free text; grounding stops invention |
| `missing_required_fields`, `complete` | code only | never taken from the model |
| any field present in `known_facts` / history | known fact | §5 |

Every disagreement is listed in `trace.rule_model_disagreements`.

## 4. Prompt design (`copilot/interpret/prompts/interp-v1.txt`, versioned)

Skeleton (system + user; few-shot as prior user/assistant pairs):

```
SYSTEM: You classify one message for a location-services sales/support copilot.
Return ONLY one JSON object, no prose. Allowed values:
intent: [8 intents]; sub_intent: [map intent -> sub_intents]; api: [4]; team: [Sales, Support].
Qualification fields: use_case_api{api,use_case}, region, monthly_volume{raw,requests_per_month,unit},
current_provider [6 canonical names or null], timeline, budget, technical_constraints, decision_maker.
Fill a field ONLY if the user stated it; copy the exact phrase into "evidence". Otherwise null.
Do not list missing fields. Do not answer the question.
FEW-SHOT (k=4-6, allowed pool only): user: <query+history> / assistant: <JSON>
USER: HISTORY: <turns, user and assistant, oldest first>  KNOWN: <known_facts JSON>
RULE_HINTS: <rule hits JSON>  MESSAGE: <query>
```

Question text is not in git. A run loads it from `TRACKB_GT_CSV` and fails if that file, the gold file, or any listed id is missing. `TRACKB_FEWSHOT=off` is the only zero-shot opt-out.

Model output shape (smaller than the full output; the code builds the rest):

```json
{"intent":"…","sub_intent":"…","api":"…","team":"…","confidence":{"intent":0.9,"api":0.9,"team":0.8},
 "qualification":{"use_case":{"value":null,"evidence":null},"region":{"value":"India","evidence":"in India"},
  "monthly_volume":{"value":"500,000 requests a month","evidence":"500,000 requests a month"},
  "current_provider":{"value":null,"evidence":null},"timeline":{"value":null,"evidence":null},
  "budget":{"value":null,"evidence":null},"technical_constraints":{"value":null,"evidence":null},
  "decision_maker":{"value":null,"evidence":null}}}
```

Parameters: `temperature=0`, `max_tokens=512`, no `response_format`, and **Qwen3 thinking disabled**. The disabling method is an **ASSUMPTION to verify against the endpoint and `inputs/learner-guide.docx`**: the guide documents the base URL, model ID, `max_tokens` 1–1024, the 8,192 combined limit, 1 active request per key, 60 requests/min, and chat/streaming/tool-call support. It says nothing about thinking mode, `chat_template_kwargs`/`enable_thinking`, the `/no_think` soft switch, JSON mode or seeds. The implementer must test these once, record the result in the trace README, and keep the `<think>` strip in the post-check either way.

Token budget (chars/4 estimates; GT `question` max 444 chars ≈ 111 tokens):

| Part | Approx tokens |
|---|---|
| System + vocab lists + field rules | ~600 |
| Few-shot, 5 pairs × ~250 | ~1,250 |
| History (cap: last 6 turns, ≤1,600 chars) + known facts + rule hints | ≤ ~550 |
| Message | ≤ ~150 |
| **Input total** | **≤ ~2,550** |
| Output (requested max 512; expected ~200–300) | ≤ 512 |
| Repair call (input + errors ≤ ~2,800; output ≤ 512) | ≤ ~3,300 |

Each call is far below the 8,192 combined limit. The client rejects any call whose estimated input + `max_tokens` exceeds 8,192 → `abstain_reason="budget_exceeded"`. Where history must be cut, the oldest turns are trimmed first.

## 5. History use

- Facts stated in earlier **user** turns count as stated. Merge order: `known_facts` (state store) → rule/model extraction on earlier user turns → the latest turn. A later explicit value overrides an earlier one.
- A field filled from history or known facts is **not** missing, and its provenance is `history` or `state`. This is what stops downstream steps re-asking answered questions (Activity 13 measures that).
- Assistant turns are context for the model only. Values are never extracted from them: in GT v2.5, the assistant line "Before I answer, one question…" is exactly what produced spurious gold timelines (§6.4).
- A user answer like "Not sure" / "leave that open" leaves the field null, so it stays missing.

## 6. Metrics (exact, reproducible)

Scope: the 80 records of `mapping/gold_outputs_frozen.jsonl` (sha256 `151f87f5…9123` at `eval-freeze-v2`, per `eval/freeze-manifest.json`). Gold labels come from that file: `intent`, `api`, `team`, `outputs.qualification_details`.

### 6.1 Intent and API accuracy
`accuracy = (# records with pred == gold, exact string) / 80`. An abstained or null prediction counts as wrong. Team accuracy is reported too (diagnostic; it has no Activity 2 threshold).

### 6.2 Qualification field-level F1 (micro)
Slots per record (8): `use_case_api` (compared on `.api` only), `region`, `monthly_volume`, `current_provider`, `timeline`, `budget`, `technical_constraints`, `decision_maker`. `use_case_api.use_case` free text is reported separately as a diagnostic.
For each record × slot, with normalised gold `g` and prediction `p`:
- `g≠null, p≠null, match(g,p)` → TP;
- `g≠null, p≠null, not match` → FP **and** FN;
- `g=null, p≠null` → FP;
- `g≠null, p=null` → FN;
- both null → ignored.

`P = TP/(TP+FP)`, `R = TP/(TP+FN)`, `F1 = 2PR/(P+R)`, summed over all 80×8 cells. If the denominator is 0, F1 = 1.0 when both counts are 0.

Normalisation `norm()` (before `match`):
- **null-likes:** `None`, `""`, whitespace, `"n/a"`, `"unknown"`, `"none"` → null.
- **text:** NFKC, lower-case, collapse whitespace, strip punctuation at either end.
- **numbers (`monthly_volume`):** compare `requests_per_month`. If null, parse `raw` with `_parse_volume_number` semantics extended to `k`/`thousand`/`M`/`million`/`lakh`(1e5)/`crore`(1e7) and commas. Match if both numbers are equal (relative tolerance 0). If either cannot be parsed, fall back to text match on `raw`.
- **region:** alias table → canonical (`india`, `indian`, `pan-india`, `in` → `India`); other values are text-normalised.
- **provider:** `PROVIDER_ALIASES` (case-folded) → one of the 6 canonical names. Unknown → text.
- **match for free-text slots** (`timeline`, `budget`, `technical_constraints`, `decision_maker`): normalised equality, or one normalised string is a substring of the other with the shorter one ≥ 3 characters.

Also reported: per-slot P/R/F1, and micro-F1 **excluding** `use_case_api`. In the frozen gold every record has a non-null `use_case_api`, so 80 of the 119 non-null gold slots (eval-freeze-v2) are API-derived and the headline F1 is dominated by API accuracy. **APPROVED (Shahid, 2026-11-14 23:37 IST):** the Activity 2 gate is the full (headline) qualification F1; F1 excluding `use_case_api` is always reported alongside.

### 6.3 Missing-field recall (precision diagnostic)
Per record, `G` = gold `missing_required_fields` and `P` = predicted. `recall = Σ|G∩P| / Σ|G|`, `precision = Σ|G∩P| / Σ|P|` (micro, 80 records). Frozen gold (eval-freeze-v2) has 284 missing entries (timeline 80, current_provider 75, region 66, monthly_volume 63), so recall mainly penalises **over-extraction** (inventing a value).

### 6.4 Gold-quality risk — RESOLVED by the mapper fix (eval-freeze-v2)
The `eval-freeze-v1` qualification gold was regex output from `mapping/map_gt.py::extract_qualification` over the whole `question` text, assistant lines included. It had spurious timelines (e.g. `"Before I answer"` on GT-LBS-DIR-020, GT-LBS-DIR-021 and GT-LBS-RG-001; 61 bad timelines in all), missed volumes (44, e.g. GT-LBS-DM-026 "20,000 per month") and 23 doubtful current providers.

**Resolution (option A, approved 2026-11-14 23:37 IST):** the mapper now reads `[user]` turns only and uses stricter `timeline`, `monthly_volume` and `current_provider` rules. The rules are documented in `mapping/gt-to-schema-mapping.md` and spec section 12. The gold was regenerated and re-frozen as `eval-freeze-v2`. Effect on the frozen 80:
- 8 timelines → null;
- 13 volumes filled;
- `current_provider` changed on 4 records (GT-LBS-AC-081, GT-LBS-AC-210 and GT-LBS-DM-094 → null; GT-LBS-AC-234 → Ola Maps).

`budget` and `decision_maker` remain null on all 80. Shahid may still spot-check; the rule-boundary cases marked "needs Shahid" are listed in the review-log entry of 2026-11-14. Any further change to the frozen gold needs a new freeze tag.

### 6.5 Scoring CLI contract (`eval/score_activity2.py`)
```
.venv/bin/python3 eval/score_activity2.py \
  --predictions runs/activity2/<run_id>/predictions.jsonl \
  --gold mapping/gold_outputs_frozen.jsonl --manifest eval/freeze-manifest.json \
  --out runs/activity2/<run_id>/
```
- **Input:** one `InterpretationOutput` JSON per line, plus `record_id`.
- The scorer **refuses to run**:
  - if the gold sha256 differs from the manifest;
  - if the record_id set ≠ the 80 manifest IDs (missing or extra records are errors, not skipped).
- **Outputs:**
  - `scores.json`: intent_acc, api_acc, team_acc, qual_f1, qual_f1_excl_api, per-slot P/R/F1, missing_recall, missing_precision, schema_valid_rate, abstain_rate, model_calls, repair_rate, thresholds, pass/fail per gate, gold sha, prompt_version, git commit;
  - `per_record.jsonl`: per record, gold vs pred for intent/api/team, per-slot TP/FP/FN with normalised values, G/P missing sets, abstain, repair_attempts;
  - `report.md`: tables by bucket, API and team.
- Pure function of its inputs: no network, no model; byte-identical output on rerun (sorted keys, fixed float format `%.4f`).
- The run that produces `predictions.jsonl` is separate (`copilot/interpret/run_batch.py`) and records the raw model responses for replay.

## 7. Data splits and leakage rules

GT v2.5: 532 records = 80 frozen (eval) + 349 non-frozen eval + 103 holdout. The frozen set spans 73 template families.

- **Few-shot and tuning pool:** `split=eval AND frozen_core=False AND template_family ∉ frozen families` → **317 records**, covering all 8 intents (smallest is human_handoff, 10), all 4 APIs (77–83 each) and both teams (Sales 145 / Support 172). 31 of them use the `[user]/[assistant]` history format. Rule-table and prompt tuning use this pool only.
- **Holdout (103):** **never** used for tuning or few-shot; it is kept for the end (Activity 22: "run exactly once, at the end"). **APPROVED (Shahid, 2026-11-14 23:37 IST).**
- **Frozen 80:** score only at named milestones (prompt version freeze, before handoff, final). Never inspected per record for prompt edits; per-record outputs go to the run folder, not into prompts.
- **Proof of no leakage** (automated test plus a line in each run's `scores.json`):
  - `fewshot_ids.json` is committed with each prompt version; a test asserts `fewshot_ids ∩ manifest.frozen_record_ids = ∅` and that no few-shot family is a frozen family;
  - a test greps every `prompts/*.txt` and `rules.py` for any frozen record ID or a ≥40-char substring of a frozen `question`: must be 0;
  - git history: prompt and rule files may change only in commits that do not also touch `runs/` frozen score outputs. Each frozen run logs the commit and prompt_version it scored.

## 8. Acceptance tests (owner in brackets)

Owner mapping (assumed; not defined in the repo): Forge = Application Engineer, Anchor = Knowledge & Validation Engineer, Assay = QA & Evaluation Engineer.

1. [Anchor] Generated Pydantic `QualificationDetails` accepts all 532 gold `qualification_details` objects and rejects 5 hand-made invalid ones (extra key, bad enum, `monthly_volume` without `raw`, wrong type, missing key).
2. [Anchor] `validator/validate_spec.py` still reports 532/532 and 80/80 pass after shared rule functions are extracted, and its regenerated outputs are byte-identical.
3. [Forge] Post-check unit tests:
   - out-of-vocab intent/api/team → invalid;
   - case variant (`"Directions"`) → normalised;
   - provider alias (`"MapmyIndia"`) → `Mappls (MapmyIndia)`;
   - sub_intent not under intent → null and logged;
   - ungrounded value → dropped;
   - `<think>` block → stripped;
   - trailing prose → first JSON object parsed.
4. [Forge] `missing_required_fields` unit tests: computed from values only. A model-supplied list is ignored. A field from history or known facts is not missing. "leave that open" stays missing.
5. [Forge] Rules-only fallback and abstain:
   - with the client stubbed to fail (timeout, 503, invalid JSON twice), output is schema-valid with `abstain=true` and a reason;
   - at most 2 model calls per input;
   - no retry loop beyond the client's 429/503 backoff (2, 4, 8 s, then stop).
6. [Forge] Client tests:
   - a missing base URL raises before any network call;
   - `OPENAI_BASE_URL`/`OPENAI_API_KEY` env values are ignored;
   - `max_tokens > 1024` is rejected;
   - estimated input + max_tokens > 8,192 is rejected;
   - the key never appears in logs or traces.
7. [Forge] Every output carries the trace fields in §2.2. `prompt_version` matches the prompt file's header.
8. [Anchor] Scorer unit tests on a synthetic 6-record fixture with hand-computed TP/FP/FN, accuracy, F1 and recall. They cover the normalisation cases in §6.2 (lakh/crore, commas, `M`, region alias, provider alias, substring rule, null-likes).
9. [Anchor] The scorer refuses a gold file whose sha256 ≠ manifest, and a predictions file with ≠ 80 IDs.
10. [Anchor] The leakage tests in §7 pass.
11. [Assay] Schema-valid rate after repair = **100%** of 80 frozen outputs (abstained outputs included, which must also be valid).
12. [Assay] Independent frozen-80 run from a clean checkout:
    - intent accuracy ≥ 0.90;
    - API accuracy ≥ 0.95;
    - qualification field-level F1 ≥ 0.85;
    - missing-field recall ≥ 0.85.

    Scored against the `eval-freeze-v2` gold (§6.4).
13. [Assay] Determinism:
    - (a) the scorer gives byte-identical `scores.json` on rerun;
    - (b) replaying recorded model responses gives byte-identical `predictions.jsonl`;
    - (c) a second live run differs by at most 1 record on intent and on API (proposed tolerance; Shahid to confirm). The temperature-0 behaviour of the endpoint is not documented.
14. [Assay] Per-bucket, per-API and per-team breakdown present in `report.md`; abstain rate and repair rate reported.

## 9. Ownership and handoff

| Owner | Delivers | Proposed paths |
|---|---|---|
| Capstone Architect | This contract; answers to interface questions; no code | `docs/activity-2-interpretation-design.md` |
| Knowledge & Validation Engineer | Pydantic models generated from `schemas/` (incl. the two wrappers in §2); shared rule functions; GT adapter / history lookup; scorer; leakage tests; any follow-up to Shahid's optional gold spot-check | `copilot/models/` (generated), `copilot/interpret/gt_adapter.py`, `eval/score_activity2.py`, `tests/eval/` |
| Application Engineer | Interpretation module, rules, prompts, post-check, repair/fallback; thin endpoint client; tracing fields; batch runner with response recording | `copilot/interpret/{__init__,interpret,rules,postcheck,models}.py`, `copilot/interpret/prompts/interp-v1.txt`, `copilot/interpret/fewshot_ids.json`, `copilot/llm/client.py`, `copilot/interpret/run_batch.py`, `tests/interpret/` |
| QA & Evaluation Engineer | Independent frozen-80 scoring at milestones, determinism checks, report | `runs/activity2/<run_id>/` (decide whether committed or archived) |

Handoff order:
1. Shahid reviews the remaining open points in §10 (gold fix, holdout and F1 gate are decided).
2. Knowledge & Validation Engineer: models, adapter, scorer (tests 1, 2, 8–10).
3. Application Engineer: client and module (tests 3–7).
4. Tuning on the 317-record pool.
5. QA & Evaluation Engineer frozen run (tests 11–14).

Agent code must not be committed before the `eval-freeze-v2` tag (the re-freeze from §6.4).

## 10. Open risks and assumptions

1. **Qualification gold defects (§6.4):** RESOLVED by the mapper fix and `eval-freeze-v2`. The gold is still rule-derived: Shahid may spot-check, and the rule-boundary provider cases are flagged "needs Shahid".
2. **Thinking mode, JSON mode, seed and temperature-0 determinism on VM07 are undocumented** in the Learner Guide. Assumed controllable; verify once and record.
3. **Holdout use for tuning:** DECIDED. The holdout is never used for tuning (§7).
4. **Headline F1 includes `use_case_api`:** DECIDED. The gate is the full F1, with F1 excluding `use_case_api` reported alongside (§6.2).
5. **No history column and no state store in GT:** history comes only from 52 inline `[user]/[assistant]` questions (5 frozen), and `known_facts` is untested by the frozen set. Activity 13 must cover it.
6. **Free-text match rule** (substring, ≥3 chars) is a proposed convention, not a defined threshold. It may be generous; per-slot reporting makes this visible.
7. **Rate limits:** 1 active request per key and 60/min. A frozen run is ≤160 calls, sequential, so batch runs must queue and must not share the key concurrently with other team runs.
8. **Owner mapping of Forge/Anchor/Assay** to roles is assumed (§8).
9. **Token counts** are `ceil(chars/4)` estimates, not tokenizer counts. The real `usage` field is recorded in the trace when available, but limits are enforced on the estimate until Activity 8 or Activity 20 starts.
10. Spec sign-off is still pending; this contract inherits the spec's definitions and changes none of them.
