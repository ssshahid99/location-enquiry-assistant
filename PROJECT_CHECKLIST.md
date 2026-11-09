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
- [ ] Baseline git commit and tag `v0.3-prototype` (done when `git log` shows it)

## Phase 1 - Build now (no waiting on people)
- [ ] A3 Trace viewer: per-case Trace tab (steps, tool inputs/outputs, retrieved sources, timings), review a past answer by trace id, filters, JSON export
- [ ] A4 Approval burden: per-case "review all" screen; each decision still recorded individually; stale-request flag (never auto-applied)
- [ ] R5 Known wrong answers fixed and re-checked: walking/cycling per provider (DIR-114), results[0] (RG-039), Ola grace period (DIR-121)
- [ ] R7 Freshness tooling: compare snapshots, re-index only changed documents, snapshot date and age warning in the UI
- [ ] A7a Count model calls per route (replace the static note in `agent_eval.py --chain`)
- [ ] Commit and tag `v0.4-phase1`

## Phase 2 - Measure and improve (needs a quiet gateway)
- [ ] R1a Rerun all 400 development questions (started 2026-11-09; record result here: ____ correct / ____ partial / ____ incorrect; number match ____; citation support ____)
- [ ] R3 Retrieval experiments: larger embedding model; small-to-big. Keep only if cited-passage recall@6 rises 5+ points and judged-correct does not drop more than 2
- [ ] A2 Customer-level history across cases (customers table, earlier facts shown with provenance and confirmed, not assumed)

## Phase 3 - Real-world feedback (needs people)
- [ ] Reviewer fills `eval/review/answer_review_v2.xlsx` (30 answers); compare with the unreviewed verdicts
- [ ] Tester round using `TESTER_GUIDE.md` (40-60 questions, multi-turn cases, rephrasings, must-be-refused items)
- [ ] R6 Mine the logs and tester findings; add facts and capabilities (verbatim quotes, tests enforce them)
- [ ] A5 Planner coverage measured on the tester messages (not the development split); model-assisted fallback only if needed
- [ ] A6 Capability library from about 9 to about 25; required-parameter lists for Google, Mapbox, HERE with quotes
- [ ] A7 Human review of 30 agent drafts and handoffs (new sheet); handoff completeness on 50+ cases

## Phase 4 - Inputs only others can give
- [ ] A1 Approved escalation policy (project doc SC-02) received and encoded; relabel "proposed" to the policy version; tests per rule
- [ ] R2 HERE prices and Ola Distance Matrix free-allowance answer; added to `pricing.yaml` with a source quote; price sweep extended
- [ ] R4 Ask the gateway owner for a stronger model; trial it with the 60-question eval before adopting
- [ ] Knowledge-base source confirmed (SC-03); human-approval points confirmed; approvers named (now: deepshikha only)

## Phase 5 - Freeze and prove
- [ ] X1 Regression gates in `eval/gates.py` (guard false positives on the 500 = 0, HERE violations = 0, Critic false positives <= 3%, no judged-correct drop > 2 points, approval gates 17/17)
- [ ] Code freeze: commit, tag `v1.0-freeze`
- [ ] Final 400 development run (record) and the **holdout 100, ONCE** (record, then never rerun)
- [ ] One-page agent report: routing, model calls per route, latency, Critic outcomes, human scores

## Project-document deliverables
- [ ] Baseline evaluation write-up: standalone model vs RAG vs agent (numbers exist; needs writing up)
- [ ] Cost-accuracy trade-off write-up: route mix (about 68% RAG / 30% agent / 1% person), model calls per route, latency
- [ ] Update the project activity tracker (RAG pipeline and evaluation tasks)
- [ ] Handover note (what exists, limits, who owns what, how to run, how to refresh)
- [ ] Optional stretch goal: compliance requirements and checks
- [ ] Optional: trace dashboard, UI login and approver roles, notifications, deployment, LangGraph wrapper
