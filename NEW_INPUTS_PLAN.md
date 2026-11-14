# New inputs (received 2026-11-14): analysis and plan

Written for: Deepshikha (project owner). Inputs analysed: the shared embedding endpoint (VM02), ground truth v2.4 (`ground-truth (1).xlsx`),
and the reviewer's `answer_review_v2.xlsx`. Numbers below were computed from the files; nothing was run against the embedding endpoint yet
(its key file was not opened).

## 1. What each input is, and will it improve the RAG?

### 1.1 Embedding endpoint: `bge-small-en-v1.5`, 384 dimensions, `POST /v1/embeddings`, 16 texts per request, 512 tokens per text
- We embed locally with `bge-base-en-v1.5` (768 dimensions). `bge-small` is the SMALLER sibling. From the published model cards (quoted from
  memory, verify before citing): retrieval average about 51.7 for small vs about 53.3 for base vs about 54.3 for large.
- **Expected effect on accuracy: none or slightly negative.** It is not the "larger embedding model" that R3 asked for.
- What it does give: the sanctioned shared infrastructure (it may be expected for the final system), no local CPU embedding, a 2x smaller index.
- Risks: one more network dependency (capacity errors, certificate `vm02-ca.crt`), a shared key, and 51 of our 1,217 chunks (4.2%) are longer
  than 512 BERT tokens (max 905), so the endpoint, like our local model, truncates them silently.
- Decision rule (unchanged from the plan): adopt only if strict recall@6 is not more than 2 points below the current setup. Try also
  combining both models (rank fusion), and a larger model run LOCALLY (`bge-large`, no infrastructure change needed).

### 1.2 Ground truth v2.4: 521 records (418 eval, 103 holdout), 27 columns, 6 tabs. This is the biggest input.
| Finding | Number | Meaning for us |
|---|---|---|
| IDs shared with our old 500 | 401 (99 removed, 120 new) | old scores are not comparable; re-baseline |
| Questions changed / reference answers changed | 131 / 209 of the 401 shared | many answers were corrected |
| HERE | 0 records (old set: 107) | **HERE is no longer in scope** (40 of the removed items were HERE; 67 kept items had HERE removed). Our HERE work (R2, HERE gate) loses its purpose |
| Mappls (MapmyIndia) | 36 new records, 0% of them answerable from our corpus | **we list Mappls as out of scope and abstain**; the ground truth now expects answers |
| Gold pages missing from our 114-page corpus | 8 of 47 cited URLs: 4 Mappls pages, Google coverage + support, Mapbox guides + tokens | 47 records (9%) cannot be answered correctly today (the 36 Mappls ones plus 11 others) |
| Holdout composition | 77 of 103 new-holdout items were in our OLD DEV set; 20 were old holdout; 6 new | the holdout is NOT unseen for us (see 1.4) |
| Frozen core (80, verified_live) | 40 were old dev, 11 old holdout, 29 new | fixed scoreboard; may not be tuned to |
| Verification | 236 verified_live, 225 carried_from_v1_unverified, 60 flagged; review_status pending for all 521 | only the 236 are trusted; the answers are not human-approved yet |
| New columns | `gold_tool_trace` (521), `expected_tools`, `gold_clarifying_questions` (182), `escalation` (should_escalate, severity SEV1-4, owner, reason), `rubric`, `cited_doc_spans` + a `citations` tab (2,894 span rows) | lets us measure the AGENT properly, and retrieval at span level |
| New structure | 8 mapped intents, 10 buckets (for example `history` = multi-turn transcripts, `updates` = "Sandbox CRM ticket TKT-xxxx, propose the field updates, do not apply until I approve") | new agent test types |

Documentation inconsistencies to raise with the coordinator: `data-usage` says "GT-EVAL working pool (400 minus core)" while the totals are
418 eval; 60 flagged records are open data-quality issues; 19 of them are the Ola "free allowance counts pairs or requests" question we also
have open.

### 1.3 Reviewer sheet: only 11 of 30 rows are filled (4 correct, 7 incorrect, 19 blank)
- Reviewer vs the Qwen judge agree on 4 of 11. The judge called 5 of the 7 "incorrect" answers correct or partial: it IS generous, as suspected.
- Reviewer vs Claude's unreviewed verdicts agree on 9 of 11 (82%; small sample; Claude's column stays marked UNREVIEWED).
- Concrete defects named by the reviewer: unnecessary INR/USD conversion shown (AC-081); wrong Google number and "pricing not available for
  any provider" (DIR-039); answer specific to iOS (RG-046); irrelevant answer (RG-039, later improved); guessing (DIR-114); price given
  without usage cost (DM-043); DIR-121 needs a clarification from the reviewer (the note and the verdict disagree).
- The sheet was generated before several fixes, so some defects are already fixed; the rest are in the plan below.

### 1.4 Something I must tell you: holdout exposure
The data-usage rules say the holdout is never looked at or tuned on. After the re-split, 77 of the 103 holdout items were development items
that we had analysed. The general fixes made from that analysis (grounding check, customer numbers, rate card) were not written for
individual items, but while analysing the grounding false-drops I read the model output for 7 items that are now holdout
(DIR-004, DM-001, DM-039, DM-084, RG-050, RG-055, RG-089) and 1 that is now in the frozen core (RG-084). Only 26 holdout items (20 old holdout + 6 new) are clean for us.
Recommendation: tell the coordinator; report holdout results on the clean 26 separately; do not run the holdout until this is settled.

## 1.5 Decisions received 2026-11-14 (Deepshikha)
- D1 HERE stays in scope: old HERE items are kept (107 rows `LEGACY:<id>`, built by `eval/build_gt_v3.py`).
- D2 Sealed holdout = never-used questions: old holdout (never touched) + the new v2.4 questions, minus the frozen core (80, a scoreboard) and minus 15 items whose template family also has a seen item. Result: 147 (v2.4) + 23 legacy HERE = 170 holdout, 378 working/dev rows, 80 core. Imbalance to report: only 11 pricing items in the sealed v2.4 holdout (98 coverage/docs/troubleshooting). `load_items("holdout")` now refuses without `allow_holdout=True`.
- D3 The shared embedding endpoint is REQUIRED: backend switch built (`EMBED_BACKEND`), probe OK (384-d, 0.9 s). Index must be rebuilt in 384-d and the guard similarity threshold recalibrated.
- D4 Mappls needs prices: searched official and third-party sources 2026-11-14; NO credible official price list is published (official pages show none; third-party figures conflict and are years old). None of the 36 Mappls ground-truth records involves price. Until Deepshikha supplies a source, Mappls prices abstain ("not published, ask Mappls for a quote"), like HERE.
- D5 The escalation labels ARE the approved policy (SC-02): A1 unblocked (encode severity, owner, reason per intent).

## 2. Decisions needed from people (before the matching code)
| # | Question | Recommended answer | Blocks |
|---|---|---|---|
| D1 | Is HERE out of scope now? | Yes: stop HERE price work; keep the abstain rule for any HERE question | R2, HERE gate |
| D2 | Holdout: 77/103 were our dev items. Reassign, or accept and report the clean 26 separately? | ask coordinator; meanwhile report separately | final run |
| D3 | Must the final system use the shared embedding endpoint? | ask mentor/coordinator | embedding choice |
| D4 | Mappls in scope with pricing, or documentation answers only? | documentation only unless prices are supplied | Mappls work |
| D5 | Are the `escalation` labels (severity, owner, reason) the approved escalation policy (SC-02)? | ask; if yes, A1 is unblocked | A1 |

## 3. Implementation plan (in order)
**Phase A. Adopt the new ground truth (about 1 day).** New loader reading the xlsx into the existing format (id, question, answer, api,
team, providers, split, difficulty, `sub_intent` for the old intent names, parsed escalation); keep old files for comparison. Evaluation
tiers: frozen core (80, scoreboard, never tuned), working pool (eval minus core), verified_live only as the "trusted" subset, holdout
untouched. Re-baseline the current system on the new eval (about 50 min of gateway) and report core, trusted and all, with the Qwen judge shown
as an upper bound only. Update `eval/gates.py` baseline to the new core.

**Phase B. Close the corpus gap (about 1 day, the surest accuracy gain).** Add the 8 URLs to `config/sources.yaml` (Mappls pages need the
browser fetcher), take a new dated snapshot, use the R7 freshness tool to compare and re-index only the new pages. Make Mappls a supported
provider (remove it from the out-of-scope list, add it to provider detection, grounding provider words, the guard's domain terms, facts and
capabilities only with verified quotes). Pricing for Mappls only if the coordinator supplies it (D4). Measure the 47 affected records
before and after.

**Phase C. Retrieval measurement at span level (about 0.5 day).** Replace document-level gold with the `citations` span rows: a chunk counts
only if it contains the gold span. Gives an honest recall@6 that the Qwen judge cannot inflate. Cap chunks at about 480 BERT tokens
so nothing is truncated (re-index affects every model option).

**Phase D. Embedding experiments (about 1 day, after D3).** E0: one probe request (dimension 384, latency, capacity behaviour). E1: embed the
corpus through the endpoint (about 77 requests of 16) and compare strict recall@6 with the local base model on core + working pool. E2: rank
fusion of both. E3: local `bge-large`. Adopt by the rule in 1.1. Whatever wins, add a switch `embed_backend = local | endpoint` with retry on
capacity errors, the CA file, the key in `.env` only (never committed), and a local fallback.

**Phase E. Fix the reviewer's findings (about 1 day).** Show the USD to INR note only when a conversion is actually used; free-tier answers
that state a price must also state the usage cost; platform-specific answers (iOS) must say which platform the source is about; recheck DIR-039,
DM-043, RG-046; ask the reviewer to finish the remaining 19 rows and clarify DIR-121; add a stricter judge based on the gold spans and the
`rubric` column. Then re-run the grounding fix (R8) on the new eval.

**Phase F. Use the agent gold data (about 2 days).** Map the gold tools to ours: `doc_search` = retrieve, `calculator` = price calculator,
`update_record` = tracker proposal (mode propose, requires human approval), `escalate_ticket` = escalation proposal. Add a mock CRM
(ticket ids such as TKT-49627) for the 50 `updates` records, a transcript parser for the 50 `history` records (must not re-ask what was
answered), and metrics: tool-set match, calculator result match, clarifying-question coverage, escalation accuracy (should_escalate,
severity, owner), proposed field match, "nothing applied before approval". Align our owner/severity vocabulary with the labels if D5 says they
are the policy.

**Phase G. Gates and freeze.** Re-baseline the gates on the new core, retire the HERE gate, then freeze, final run on core + eval, and the
holdout once, as decided in D2.

## 4. What I expect, stated cautiously
- Certain: the corpus gap (Phase B) fixes a known 9% of the questions. Everything else is measured first.
- Not expected: a gain from `bge-small` itself.
- Unknown until Phase A: how our current system scores on the corrected ground truth; the old 67% is not comparable.
