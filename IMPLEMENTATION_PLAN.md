# Implementation plan: all MUST and SHOULD items (RAG + agent)  - drafted 2026-11-09

Scope: every item marked "must do" or "should do" in the RAG and agent lists of BACKLOG.md. "Could do" items are out of scope here.
Working-time estimates are for the engineering work only; waiting on people (tester, reviewer, team, vendors) is shown separately.
IDs: R = RAG, A = agent, X = cross-cutting.

## 1. Work items

| ID | Item | Type | Steps | Done when (acceptance) | Depends on | Effort |
|---|---|---|---|---|---|---|
| X0 | Prerequisites: dependency file, tester guide, README agent section, git commit + tag `v0.3-prototype` | must | `requirements.txt` from the venv (pinned); tester guide (what to try, how to log); README section; commit with `.env` / `data/` / reports excluded; tag | a clean machine can install and run; `git status` clean; tag exists | none | 0.5 d |
| R1 | Measure properly | must | (a) rerun 400 dev questions with grounding + marker fix; (b) human review of v2 sheet; (c) tester round; (d) holdout 100 ONCE after freeze | numbers recorded with config hashes in `eval/reports/`; holdout run once and never repeated | X0, tester, reviewer, a freeze (X1) | 0.5 d + waiting |
| R2 | Close the data gaps | must | HERE rate card + Ola Distance Matrix free-allowance answer from the vendors; add to `pricing.yaml` with a source quote; extend the price sweep; flip the Ola switch if needed | HERE questions get verified numbers (or still abstain if no source); sweep passes for the new entries | team / vendors | 0.5 d |
| R3 | Better passage retrieval | should | experiment 1: larger embedding model (re-embed ~25 min); experiment 2: small-to-big (retrieve small, send the parent paragraph); measure with `eval/chunk_recall.py` and a 60-question answer eval | KEEP only if cited-passage recall@6 rises by 5+ points AND answer eval does not regress; otherwise restore the backup | X0 | 1.5 d |
| R4 | Stronger model on the gateway | should | request to the gateway owner; script to swap `chat_model`; rerun 60-question eval; adjust prompts if needed | decision recorded with before/after numbers | gateway owner | 0.5 d + waiting |
| R5 | Fix known wrong answers | should | DIR-114 (walking/cycling per provider), RG-039 (results[0]), DIR-121 (Ola grace period): add or correct facts / capabilities with verbatim quotes; rerun those questions | the three answers are correct on rerun; facts tests pass | none | 0.5 d |
| R6 | Grow facts and capabilities from real questions | should | script that mines the logs for abstained / ungrounded / low-citation / reviewer-flagged answers; triage; add entries with quotes; tests enforce quotes | each tester-found failure family is either fixed or logged with a reason | tester round, reviewer | 1 d, repeated |
| R7 | Documentation freshness | should | re-fetch command that compares page hashes between snapshot dates and lists changed pages; re-index only changed documents; show the snapshot date in answers; monthly reminder | a refresh run reports what changed and re-embeds only that | X0 | 1 d |
| A1 | Approved escalation policy (SC-02) | must | obtain the policy; encode it in `escalation_rules.yaml` + `planner.yaml`; relabel "proposed" to the policy version; tests per policy rule; rerun escalation eval | every policy rule has a test; recommendations cite the policy version | team provides the policy | 0.5 d + waiting |
| A2 | Customer-level history across cases | must | `customers` table; link cases to a customer; at case start load earlier cases and facts; Planner marks reused facts "from case X, please confirm" instead of re-asking; UI customer view; PII-safe identifiers | a returning customer's earlier facts are used and shown with provenance; tests for matching, confirm-not-assume, and privacy | X0 | 1.5 d |
| A3 | Trace viewer | must | "Trace" sub-tab per case (steps, tool inputs/outputs, retrieved sources, timings); "review a past answer by trace id" in Ask; filters for abstained / escalated / critic verdict; JSON export | an investigator can reconstruct any answer or case without the database | none | 0.5 d |
| A4 | Approval burden | should | per-case "review all" screen with per-item toggles; each decision still recorded individually by the single approver; stale-request flag after N days (never auto-applied) | an incident needs one screen and one confirmation; the gate tests (17) still pass | none | 0.5 d |
| A5 | Planner coverage | should | build a labelled message set from tester logs (NOT the development split); measure classification and routing on it; add a model-assisted fallback only for "unclear" messages with domain signals (JSON validated by code, fail-safe to "unclear") | accuracy on the tester set is reported honestly; fallback costs no extra calls unless triggered | tester round | 1 d |
| A6 | Capability library + required parameters | should | extend capabilities from ~9 to ~25 (drawn from the capability_* question types); required-parameter lists for Google / Mapbox / HERE from the vendor reference with verbatim quotes | all quotes verified by tests; Executor reports gaps for anything undocumented | none | 1.5 d |
| A7 | Honest agent evaluation | should | count model calls per route; handoff completeness on 50+ cases; a human-judged review sheet of 30 agent drafts and handoffs; routing eval on the tester set; automate the 8 demo scenarios as an acceptance test | a one-page report: routing, calls per route, latency, Critic outcomes, human scores | R1, tester round, reviewer | 1.5 d + human time |
| X1 | Regression gates and freeze | must | thresholds written into `eval/gates.py`; config hashes in every report; code freeze tag before the holdout | gates pass before the holdout run | all above | 0.5 d |

## 2. Phases and order

| Phase | Contents | Can run while waiting on people? |
|---|---|---|
| 0. Prepare (0.5 d) | X0 | yes |
| 1. Build now (about 3 d) | A3 trace viewer, A4 approval bundling, R5 known wrong answers, R7 freshness tooling, A7 model-call counting | yes; no gateway needed except small checks |
| 2. Measure and improve (about 3.5 d) | R1(a) 400 rerun (about 50 min of gateway time), R3 retrieval experiments, A2 customer history | the rerun and re-embed need a quiet gateway |
| 3. Real-world feedback (1-2 weeks of calendar time) | tester round + human review (R1 b, c), then R6 mining, A5 coverage, A6 libraries | depends on tester and reviewer |
| 4. Inputs from others | A1 policy, R2 vendor data, R4 stronger model | each is independent; do as they arrive |
| 5. Freeze and prove (about 1.5 d) | X1 gates, final 400, holdout 100 once, A7 final report | after everything else |

Estimated engineering effort: about 14 working days in total (several overlap with waiting time).

## 3. Who does what
- Me: all code, tests, experiments, evaluation runs, write-ups.
- You / your team: provide the escalation policy (A1), obtain HERE prices and the Ola answer (R2), ask the gateway owner for a stronger model (R4), name approvers, decide when to freeze.
- Tester: 40-60 realistic questions plus multi-turn cases; log results (R1c).
- Reviewer: judge the 30-answer sheet, later the 30 agent drafts (R1b, A7).

## 4. Decision gates (so changes are kept only if they help)
- Retrieval change kept only if cited-passage recall@6 rises 5+ points and judged-correct does not drop more than 2 points.
- No change may raise guard false positives on the 500 questions above 0, HERE price violations above 0, or Critic false positives above 3%.
- Routing quality is judged on the tester-message set, not the development split it was tuned on.
- The holdout is run exactly once, after X1.

## 5. Risks
| Risk | Effect | Mitigation |
|---|---|---|
| Gateway serves one request at a time | long evals block the demo and testers | schedule evals when the app is idle; keep stubs in tests |
| Escalation policy never arrives | A1 stays "proposed" | keep the label, record the gap in the report |
| Tester/reviewer delays | R6, A5, A7 slip | build Phase 1 and 2 first; they do not need them |
| Better embeddings are slower on CPU | slower retrieval, longer re-embed | measure latency; keep the old index backup |
| Customer history risks mixing up people | wrong facts reused | confirm-not-assume rule, provenance on every reused fact, privacy tests |
| Model swap changes behaviour | regressions | rerun the gates and the 60-question eval before adopting |
