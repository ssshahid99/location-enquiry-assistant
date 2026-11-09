# Project checklist - resume here

Last updated: 2026-11-09. Tick items as you finish them (`[x]`). Detail for each item is in `IMPLEMENTATION_PLAN.md`; history in `SESSION_NOTES.md`.

## RESUME HERE (state when this was written)
- **Built and working:** RAG pipeline, price calculator, guard, Planner, Executor, Critic, Router, Orchestrator, approvals (single approver), cases with close/reopen, UI (Ask | Cases | Approvals), API, evaluation scripts. UI confirmed working by you.
- **Running or waiting:** the 400-question development rerun was started on 2026-11-09 (about 50 minutes). Result file: newest `eval/reports/answer_eval_*.jsonl`; summary is the last lines of its console output. Compare with the previous full run: **276 correct / 99 partial / 25 incorrect, headline-number match 92.9%, citation support 78.0%, uncited 5, HERE violations 0/55.**
- **Next 3 actions:** (1) read the rerun result and record it below; (2) finish Phase 1 (trace viewer, approval bundling, three known wrong answers, freshness tooling, model-call counting); (3) hand `TESTER_GUIDE.md` to a tester and the review sheet to a reviewer.
- **Start the app:** `cd C:\Users\Deepshikha\lbs-copilot; .\.venv\Scripts\Activate.ps1; $env:PYTHONPATH=".;src"; streamlit run app/streamlit_app.py` (http://localhost:8501). Needs your network access to the model gateway. For a tester on the same network add `--server.address 0.0.0.0`.
- **Check everything still works:** `python -m pytest tests -q` (about 370 tests, offline, about 25 s).
- **Rules to remember:** the 100-question holdout is run ONCE, after the freeze; one approval is always enough; nothing is ever sent externally; keep `.env` and the certificate out of git; the gateway serves one request at a time (do not run evals while someone is using the app).

## Phase 0 - Prepare
- [x] Secret scan before the first commit (found and removed a real-key fragment from a test; gateway address removed from committed defaults)
- [x] `requirements.txt` (203 pinned packages), `.gitignore` extended, `.env.example` cleaned
- [x] `TESTER_GUIDE.md` and `tester_log_template.csv`
- [x] README rewritten for the agent layer
- [x] Baseline git commit and tag `v0.3-prototype` (116 files, no secrets; repository is local only, nothing pushed anywhere)

## Phase 1 - Build now (no waiting on people)
- [x] A3 Trace viewer: per-case Trace tab, top-level Traces tab with filters, API `/traces`, `/traces/{id}?full=true` (JSON export), `/cases/{id}/trace`; model calls stored per trace (6 tests; 377 pass). Not yet viewed by a person in the running UI: restart Streamlit to see it.
- [x] A4 Approval burden: Cases > "Review all (n)" screen, API `/cases/{id}/review` and `/review/decide`; each request still decided and recorded individually, stale requests (customer wrote again) need an explicit tick to approve, one failure never blocks the rest, nothing auto-approved (8 tests). Not yet clicked through by a person.
- [~] R5 Known wrong answers: in the 2026-11-09 rerun DIR-114 and DIR-121 are judged correct. RG-039 was still partial (answered ROOFTOP/street_address); added fact `google-reverse-geocode-which-result` with a verbatim quote that deliberately does NOT claim results[0] is always the most specific (the fetched page does not say so). RG-039 rerun: the answer now advises filtering by result_type/location_type and says the first result is only typically the most specific, "not guaranteed" (no longer ROOFTOP-only); not re-judged. DIR-114 still omits HERE.
- [x] R7 Freshness tooling: `python -m lbs_rag.ingest.freshness age|compare|reindex [--apply]` compares PARSED page text between dated snapshots (page noise ignored), re-chunks and re-embeds only changed pages (dry run unless --apply, backs up chunks.jsonl), UI shows the snapshot dates and a warning after 45 days (`config/freshness.yaml`) (6 tests). Real use needs a second snapshot (`python -m lbs_rag.ingest.fetch`): only one exists (2026-11-08), so `compare` has not been run on real changes yet. After a refresh the tests re-verify every verbatim quote: a failing quote is a changed fact.
- [ ] A7a Count model calls per route (replace the static note in `agent_eval.py --chain`)
- [x] Commit and tag `v0.4-phase1` (commit 6dce853, local only; git is at C:\Program Files\Git\cmd\git.exe, not on PATH in the VS Code shell)

## Phase 2 - Measure and improve (needs a quiet gateway)
- [x] R1a Rerun of all 400 development questions DONE 2026-11-09 (`eval/reports/answer_eval_20261109_140518.jsonl`): **268 correct / 94 partial / 38 incorrect** (was 276/99/25), headline-number match 92.9% (unchanged), **citation support 83.0%** (was 78.0%), cited-source hit 95.4%, uncited 5, HERE violations 0/55, abstained 26 (22 more than before). Gates all PASS but judged-correct is down exactly 2.0 points (the limit). Cause found: the grounding check now abstains on answers that were judged correct before (8 correct -> abstained; 4 of them error_resolution; e.g. DM-044 MAX_ELEMENTS_EXCEEDED, DIR-004, DIR-073). Judge counts an abstention as incorrect. Trade: higher citation support, fewer unsupported sentences, but more "could not verify" replies. NEXT: R8 below.
- [ ] R8 Grounding check over-abstains (found by R1a): look at the 22 new abstentions, find which removed claims were actually supported (for example computed totals such as 15x10=150, paraphrases), loosen only where the passage really supports it; then rerun the 400 and require judged-correct back to >= 276 with citation support >= 80%. Do not weaken the check without that evidence.
- [ ] R3 Retrieval experiments: larger embedding model; small-to-big. Keep only if cited-passage recall@6 rises 5+ points and judged-correct does not drop more than 2
- [x] A2 Customer-level history (code done): earlier cases listed, earlier SETUP facts suggested with case and date, used only after a person confirms (or corrects) them; incident facts never carry over; `config/customers.yaml`; API `/cases/{id}/customer` (7 tests). Customers are matched by the name typed on the case (no separate table); not yet clicked through by a person.

## Phase 3 - Real-world feedback (needs people)
- [ ] Reviewer fills `eval/review/answer_review_v2.xlsx` (30 answers); compare with the unreviewed verdicts
- [ ] Tester round using `TESTER_GUIDE.md` (40-60 questions, multi-turn cases, rephrasings, must-be-refused items)
- [ ] R6 Mine the logs and tester findings; add facts and capabilities (verbatim quotes, tests enforce them)
- [~] A5 Planner coverage: the model-assisted fallback is BUILT and OFF (`config/planner.yaml model_fallback.enabled`; validated labels only, never facts/escalations/approvals; 8 tests). Still to do after the tester round: measure with `python eval/planner_eval.py --messages tester.csv [--fallback]` and switch it on only if it fixes clearly more than it breaks.
- [~] A6 Capability library 8 -> 24 entries, every quote verified against the fetched pages (70 tests); required-parameter lists added for Mapbox (Directions, Matrix, Reverse Geocode, Search Box), Google Reverse Geocode and HERE Matrix (`config/required_params.yaml`, quotes verified). Google Directions / Distance Matrix have NO list: the fetched pages do not state one, so the Executor says "no verified list". Still to do after the tester round: add capabilities testers actually ask about.
- [~] A7 Agent evaluation: DONE: model calls measured per route; handoff completeness on 60 real cases = 58/60 complete (96.7%; the 2 gaps are person-routed cases where no draft is made by design; only 2 of 60 were person-routed, so this says little about that route); 8 demo scenarios automated (`tests/test_demo_scenarios.py`); Critic seeded-claim rates unchanged (false positives 1.2%, wrong-provider claims caught 82.4%). TO DO: a person reviews `eval/review/agent_review.xlsx` (30 drafts, generated, AI-verdict column blank); routing eval on the tester messages; one-page agent report.

## Phase 4 - Inputs only others can give
- [ ] A1 Approved escalation policy (project doc SC-02) received and encoded; relabel "proposed" to the policy version; tests per rule
- [ ] R2 HERE prices and Ola Distance Matrix free-allowance answer; added to `pricing.yaml` with a source quote; price sweep extended
- [-] R4 Stronger model: NOT POSSIBLE (decision 2026-11-09: only `Qwen/Qwen3-8B` is available and the infrastructure cannot change). All quality work stays on this model; prompts, code checks and retrieval are the levers.
- [ ] Knowledge-base source confirmed (SC-03); human-approval points confirmed; approvers named (now: deepshikha only)

## Phase 5 - Freeze and prove
- [x] X1 Regression gates: `python eval/gates.py` (guard false positives on the 400 dev questions = 0, approval gates 17/17, Critic false positives <= 3%, HERE violations = 0, judged-correct drop <= 2 points vs saved baseline `eval/baselines/answer_eval_baseline.json` = 276/400). First run: all PASS. Exit code 0 pass / 1 fail / 2 not fully checked. Re-save the baseline with `--set-baseline` only for a run you accept. `--include-holdout` adds the 100 holdout questions to the guard check: freeze only.
- [ ] Code freeze: commit, tag `v1.0-freeze`
- [ ] Final 400 development run (record) and the **holdout 100, ONCE** (record, then never rerun)
- [ ] One-page agent report: routing, model calls per route, latency, Critic outcomes, human scores

## Project-document deliverables
- [ ] Baseline evaluation write-up: standalone model vs RAG vs agent (numbers exist; needs writing up)
- [ ] Cost-accuracy trade-off write-up: route mix (about 68% RAG / 30% agent / 1% person), model calls per route, latency
- [ ] Update the project activity tracker (RAG pipeline and evaluation tasks)
- [ ] Handover note (what exists, limits, who owns what, how to run, how to refresh)
- Optional extras (trace dashboard, UI login and approver roles, notifications, deployment, LangGraph wrapper, compliance stretch goal) are parked in `BACKLOG.md` section D, not tracked here.
