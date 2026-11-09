# Backlog (updated 2026-11-09, after Router / Orchestrator / evaluation harness / UI integration)

## Built (363 tests pass)
RAG pipeline (fetch, parse, index, hybrid retrieval, rewrite, provider routing, grounded cited answers, abstention), price calculator, free-tier answers,
auditable facts and capability library (verbatim quotes), scope/ethics guard, PII/secret redaction, Planner, Executor, Critic, Router, Orchestrator
(pause/resume), approvals (single approver from `config/approvers.yaml`, internal updates only, nothing sent), approved tracker, case history/facts/timeline,
per-step traces, FastAPI, Streamlit UI (Ask | Cases | Approvals), evaluation scripts, README, AGENT_PLAN, SESSION_NOTES.

> The must-do and should-do items below are broken into steps, owners, acceptance criteria, phases and decision gates in **IMPLEMENTATION_PLAN.md**.

## A. To finish before calling the prototype complete
- [x] Cases / Approvals / Close-reopen tabs clicked through in a browser by Deepshikha: "working as expected" (2026-11-09). Case close/reopen added (authorised approver + written note; waiting approvals are cancelled, never applied).
- [x] Live-model chain evaluation DONE (`python eval/agent_eval.py --chain 24`, 18 real non-price development questions): agent route mean 12.4 s, direct RAG route 5.3 s; Critic 10/10 pass; handoff ready 10/10;
      pending approvals per case mean 3.1 (max 4). Small sample; the "model calls per message" line in the script's output is a static note, not a measurement.
- [ ] Rerun all 400 development questions with the grounding check + marker fix (answer quality was last measured BEFORE both; last full run 276/99/25 judged correct/partial/incorrect).
- [ ] Human review of `eval/review/answer_review_v2.xlsx` (30 answers; Claude's verdicts are unreviewed; the Qwen judge is generous).
- [ ] Tester round (40-60 realistic questions, rephrasings, things that must be refused, multi-turn cases). Log findings; add facts / guard phrasings from them.
- [ ] Holdout 100: run ONCE after the final changes; never tune against it.
- [ ] `requirements.txt` (missing: nobody can install from scratch), `git commit` (nothing committed; keep `.env` out), README section for the agent layer, tester guide, handover note, short demo script.

## B. Decisions / information only your team or the vendors can give
- [ ] Approved escalation policy source (project doc SC-02). Our rules (`config/escalation_rules.yaml`, `config/planner.yaml`) are a PROPOSAL until it exists.
- [ ] Knowledge-base source confirmation (SC-03): we built it from the 114 ground-truth links.
- [ ] Confirm the human-approval points (operational updates, escalations, external sends, other consequential actions) and who the approvers are (now: deepshikha only; one approval is always enough).
- [ ] HERE list prices (page never rendered; answers abstain). Ola Distance Matrix free allowance: pairs or requests (`pricing.yaml`: pairs, UNCONFIRMED).
- [ ] Ask the gateway owner for a stronger model than `Qwen3-8B` (one-line change in `config/settings.py`; likely the biggest gain on explanatory questions).
- [ ] Technology stack / repository / model provider are still "TBD" in the project document.

## C. Quality improvements (accuracy, safety, usability)
- [ ] Passage retrieval is the main accuracy limit (cited passages reach the model ~48% in the top 6). Try a larger embedding model (re-embed ~20-30 min) and small-to-big retrieval; keep only if `eval/chunk_recall.py` improves.
- [ ] Grow `config/facts.yaml` and `config/capabilities.yaml` (9 capabilities today) from tester questions; each entry needs a verbatim quote (tests enforce it).
- [ ] Planner is regex-only: knowledge-style questions are often "unclear"; unusual phrasing can be missed. Consider a model-assisted fallback (JSON validated by code) if testers hit this.
- [ ] Approvals burden: an incident can queue 3-4 requests (owner action, escalation, reply draft, tracker update). Consider bundling per case ("approve all").
- [ ] Required-parameter check covers Ola only (spec is machine-readable); other providers need a verified list.
- [ ] Critic limits: catches unsupported / unsafe / incomplete material, cannot tell a plausible, well-sourced-looking claim is wrong. Wrong-provider claims caught 82% in the seeded test; 3 of 17 missed.
- [ ] Keyword guard can miss cleverly worded harmful requests; add phrasings as found.
- [ ] Remaining answer failures seen in review: DIR-114 (walking/cycling per provider partly guessed), RG-039 (results[0]), DIR-121 (Ola grace-period logic; fact rewritten, not rerun).
- [ ] Mapbox price tables parse one cell per line (numbers come from config); combined "Autocomplete + Place Details" totals not summed; price sweep not yet covering Mapbox standard Search Box / Autofill / Place Details Pro / Ola DM "requests" mode.
- [ ] Draft replies are templates (no model wording polish); optional.

## D. Project-document deliverables and housekeeping
- [ ] Baseline evaluation write-up (standalone LLM vs RAG vs agent): numbers exist (`eval/baseline_llm.py`, 60-question A/B), not written up for the team.
- [ ] Cost-accuracy trade-off write-up (minutes' consideration): routing mix (~68% RAG / 30% agent / 1% person), model calls per route (agent chain adds none beyond the RAG call), latency (from the chain eval).
- [ ] Update the project activity tracker (RAG pipeline tasks; evaluation tasks).
- [ ] Stretch goal (optional): compliance requirements and checks.
- [ ] Optional: trace dashboard (Langfuse needs Docker; SQLite traces exist), notifications, login for the UI (none today), deployment, multi-approver roles.
- [ ] Note: the agent layer is plain Python orchestration (not LangGraph); it has explicit state, pause/resume and tests. LangGraph can wrap it later if the team wants it.
