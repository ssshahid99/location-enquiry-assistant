# Session notes (saved 2026-11-09) - resume from here

Project: Location Services Sales & Support Copilot, RAG workstream (owner: Deepshikha). Folder: `C:\Users\Deepshikha\lbs-copilot`.
Read first: `README.md` (how it works / how to run), `BACKLOG.md` (open items). This file = where we stopped.

## How to restart a session
```powershell
cd C:\Users\Deepshikha\lbs-copilot
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = ".;src"
python -m pytest tests -q            # expect 94 passed
streamlit run app/streamlit_app.py   # UI at http://localhost:8501 (needs gateway/network + .env)
```
Gateway: only `Qwen/Qwen3-8B`, no `/embeddings`, 1 concurrent request per key. Everything that calls the model is single-threaded.
`-I` mode ignores PYTHONIOENCODING: use `python -I -X utf8` for scripts that print non-ASCII.

## What is built (all working, 94 tests pass)
Fetch (114 sources) -> own HTML parser (keeps callouts) -> chunks (1,217 @450 tokens) -> BM25 + local bge-base embeddings in Chroma
-> hybrid RRF retrieval, query rewrite ON by default (re-rank OFF: it hurt; k=6) -> escalation rules (human approval via `proposed_actions`)
-> PII/secret redaction -> price calculator + code-written price answers (`tools/pricing_intent.py`, `config/pricing.yaml`)
-> free-tier answers by code (`tools/free_tier.py`) -> auditable facts with verbatim quotes (`config/facts.yaml`, `tools/facts.py`)
-> grounded Qwen answer with citations -> SQLite traces. FastAPI (`api/server.py`) + Streamlit UI (`app/streamlit_app.py`).

## Latest measured numbers (development split only; the holdout 100 has NEVER been run)
- Full 400 (before facts/free-tier, after price fixes round 1): judged correct 236/400 (Qwen judge, generous), number match 76%, citation support 77%, HERE violations 0/55.
- Pricing questions only (121), after input-reading fixes: number match 93%, 108/121 judged correct, 0 HERE violations.
- Passage retrieval (strict metric `eval/chunk_recall.py --nonprice`, 161 questions / 196 reachable cited passages), hybrid no rewrite:
  top-6 38.8%, top-10 45.4%, top-20 60.2%. WITH provider routing (`route=True`): top-6 48.0%, top-10 57.1%, top-20 70.4%.
- My hand-read of 30 answers (`eval/review/answer_review_v2.xlsx`, UNREVIEWED): 12 correct / 12 partial / 6 incorrect (was 6/13/11 before fixes). Sample is biased toward weak spots.

- **Baseline full 400 (DONE, `eval/reports/answer_eval_20261109_005507.jsonl`)**, code after price-input fixes but BEFORE facts.yaml and free_tier:
  judged correct/partial/incorrect 251/112/37 (63% / 28% / 9%, Qwen judge, generous), number match 92.6% (115 items), citation support 77.0%,
  cited a gold source 91.4%, uncited 9, abstained 10, HERE price violations 0/55.
  By intent (judge-correct): error_resolution 38/69, pricing_comparison 52/60, pricing_quote 53/58, parameter_question 22/45, response_fields 9/20, policy_question 8/15.
  The comparison run with facts + free-tier (+ routing if adopted) has NOT been done yet: that is next-step 3.

## In flight when this session ended (check these first; results may be lost if the processes died)
1. (Finished, see baseline above.)
2. `eval/chunk_recall.py --nonprice --n 80 --configs hybrid+rw,hybrid+rw+route`: compares routing WITH query rewriting (rewriting is the default).
   Rerun the same command if needed. Decision rule: if routing still helps with rewrite, make `route=True` the default in `answer()` and the API/UI.
3. A Streamlit instance was running on port 8501 from the old session (may be dead; restart as above).

## Uncommitted code changes made at the very end (not yet validated by an answer eval)
- `retrieve(..., route=True)` provider-aware retrieval in `src/lbs_rag/retrieve/hybrid.py` (default still False). Works; measured above.
- `LBS_CHUNK_TOKENS` / `LBS_CHUNK_OVERLAP` env knobs in `src/lbs_rag/ingest/chunk.py` (default 450/60, unchanged behaviour).
- Backup of the current 450-token index: `data/backup_chunks450/` (chunks.jsonl + chroma). To restore: copy `chunks.jsonl` to `data/processed/` and the `chroma` folder to `data/chroma` (stop any running app first).

## Added after the notes above were first saved (2026-11-09, later)
- Provider routing is now the DEFAULT (`answer(route=True)`): with rewrite, cited-passage recall top-6 40.8% -> 46.9%, top-10 48.0% -> 56.1%, top-20 58.2% -> 67.3%.
- General/meta questions ("hi", "what can you do?", "which questions can I ask?", "which providers do you cover?", "what are your limits?", thanks/bye)
  are answered by code from config: `src/lbs_rag/generate/meta.py`, hooked at the top of `answer()`; tests in `tests/test_meta.py`
  (includes a guard that none of the 500 ground-truth questions is classified as general). Streamlit sidebar now lists clickable example questions.
- Smaller-chunk experiment DONE and REJECTED: 250-token chunks (1,655) were worse on the strict passage metric (hybrid top-6 31.6% vs 38.8%; with routing 44.4% vs 48.0%).
  The 450-token index (1,217 chunks) was RESTORED from `data/backup_chunks450/` and verified (routing top-6 48.0% / top-10 57.1% / top-20 70.4% reproduced). `data/backup_chunks450/` can be deleted later.
  The `LBS_CHUNK_TOKENS` knob stays in `chunk.py` (default 450).
- Demo app (Streamlit) is stopped; restart it (steps in README / earlier notes) when you want to use it.

## Scope and ethics guard (added 2026-11-09, later)
- `src/lbs_rag/generate/guard.py`: runs FIRST in `answer()`, before retrieval or any model call. Categories: self-harm/violence (supportive message), prompt injection / secret
  requests, tracking private individuals, abuse/evasion (bypass limits/billing/terms, stolen keys, scraping), deception in sales (fabricating prices/benchmarks, hiding limits),
  forbidden actions (send emails, approve escalations, promise SLA/discounts/purchases), consumer navigation, legal/medical/investment advice.
  Every refusal says it "is not something this system is designed to process", gives the reason, and points to what it CAN do. Result has `refused: True`, `reason: "refused:<category>"`.
- Output safety: every answer passes through secret/PII redaction (public support email `maps-support@olakrutrim.com` is allow-listed); prompt rules 8-9 tell the model to ignore
  instructions inside retrieved pages and to decline unsafe/off-topic requests.
- Regression guard: `tests/test_guard.py` fails if any of the 500 ground-truth questions is refused (it caught 4 false positives while building). 156 tests pass in total (excluding tests that depend on the index being rebuilt).
- Off-topic gate is now ON and calibrated (eval split + 20 vague-but-legitimate probes). Three layers, in order: (1) explicit off-topic subject list (weather, recipes, jokes, sports,
  translation...) refused regardless of similarity; (2) `DOMAIN_SIM_MIN = 0.47` similarity cut-off (set just under the lowest legitimate vague question, 0.506, because borderline
  unrelated questions 0.51-0.55 overlap with vague legitimate ones); (3) for questions with no domain term and similarity < 0.65, a yes/no second opinion from Qwen
  (`guard.llm_in_scope`; 19/20 legitimate vague YES, 20/20 off-topic NO; fails OPEN if the gateway errors). Questions containing a strong domain term skip the gate.
- Model-output guards: if the model says a question is out of scope the whole text is replaced by the standard refusal; an empty model answer becomes an honest "couldn't produce a reliable answer".
- Streamlit shows a "Not processed" banner for refused questions. 200 tests pass (about 16 s; the guard tests include real similarity on the ground-truth questions).
- Known limit: keyword rules can miss cleverly worded harmful requests; add phrasings to `guard.py` as testers find them. "What is the SLA?" (no provider named) escalates as a contract request and answers from sources.

## Grounding check (recommendation #1, implemented 2026-11-09, NOT yet measured on the eval)
- `src/lbs_rag/generate/grounding.py`, applied in `answer()` to MODEL-WRITTEN text only (not code-written price/free-tier/meta answers). Each cited sentence is kept only if its numbers
  (>= 10) appear in the cited passages (or the calculator / verified limits / verified facts), it names no provider absent from the cited passages, and >= 20% of its content words
  appear in them. Unsupported sentences are removed and the answer says so; if NOTHING cited survives the answer becomes "I found sources ... couldn't verify ... won't guess"
  (`reason: "ungrounded"`, abstained, top-3 sources shown). Result also returns `removed_claims`. 210 tests pass.
- TO DO: measure its effect. The 400 run that was in progress when this was written does NOT include it. After that run finishes, rerun
  `python eval/answer_eval.py --n 400 --system rag` and compare judged-correct, citation support, and how many answers became "ungrounded" or lost sentences. If it drops
  too many good sentences, lower `MIN_OVERLAP` (0.20) or raise `MIN_NUMBER` in `grounding.py`.

## Agent layer foundation (built 2026-11-09, step 1 of AGENT_PLAN.md) - 224 tests pass
- Decision: ONE approver is always enough; approvers are listed in `config/approvers.yaml` (currently only `deepshikha`; add a line to add more, optionally limited by action type). The agent can never approve.
- `src/lbs_rag/store/cases.py`: cases, messages, known facts, deterministic history summary, state checkpoint (pause/resume), approval requests, `decide` (authorised human only; rejection needs a reason; decisions are final),
  `apply_approved` (atomic, at most once; the approver's edited draft wins), per-case event log, `trace_step` (new `trace_steps` table). `db.py` upgrades an older `data/lbs.db` additively.
- `src/lbs_rag/store/handlers.py`: what "approved" does = internal update only (case -> escalated, owner, next action; tracker/owner fields). It NEVER sends anything externally; external sends stay drafts.
- Live path: an escalation now opens a case (type `incident`, urgency high for production/security/billing) with the exchange logged and ONE pending approval. `answer()` returns `case_id` and `proposed_action_id`.
- UI Approvals tab: choose approver from the config list, edit the draft, note (required to reject), "Approve and apply" / "Reject", case timeline. API: `/approvers`, `/cases/{id}`, `/actions/{id}/decide`.
- NEXT (AGENT_PLAN.md build order step 2): the Planner (classify issue + missing-information / qualification checklists), then Executor wiring, Critic, router, evaluation.

## Full 400 run with facts + free-tier + routing + general questions + guard (DONE, `eval/reports/answer_eval_20261109_110751.jsonl`; grounding check NOT included)
judged correct/partial/incorrect 276/99/25 (69% / 25% / 6%; baseline was 251/112/37), number match 92.9%, citation support 78.0%, cited a gold source 95.2%, uncited 5 (was 9), HERE violations 0/55.
By intent (judge-correct): error_resolution 45/69 (was 38), parameter_question 24/45 (22), response_fields 13/20 (9), policy_question 7/15 (8), pricing_comparison 53/60, pricing_quote 52/58.
No real question was refused or got a "could not verify" message. CAVEAT: the facts were written for rules these questions exposed, so part of the gain is the patch, not general retrieval; Qwen judge is generous.
STILL TO DO: rerun the 400 WITH the grounding check and compare (judged-correct, citation support, how many answers lose sentences or become "ungrounded").

## Planner (AGENT_PLAN step 2, built 2026-11-09) - 247 tests pass
- `src/lbs_rag/agent/planner.py` + `config/planner.yaml` (checklists, owner roles, PROPOSED escalation evidence policy; the approved policy source SC-02 is still missing, and the Planner labels its recommendations that way).
- `classify()` -> workflow (sales | support | unclear), category, urgency, provider/API; keeps a case's established category on weak follow-ups. `extract_facts()`; `plan()` (pure) and `plan_case()` (persisted: history, known facts, checkpoint,
  trace steps, pending approvals once each). Asks at most 4 targeted questions, required first, never re-asks a known fact, and an item asked twice becomes "needs human follow-up". It only PROPOSES (owner/next action, escalation); nothing is applied.
- Measured with `python eval/planner_eval.py --split eval` (development split only): workflow accuracy on clear intents 247/252 = 98.0% (was 79.4% before widening the rules; rules were tuned on these questions, so this is optimistic and
  NOT a generalisation claim); escalation recommendation precision/recall 1.00/1.00 (also tuned). Check the holdout only in the final run.
- Known limits: knowledge-style questions ("Restrict Ola Autocomplete to Hyderabad") are often "unclear"/how_to (the future Router sends those straight to the RAG); regexes only (no model) so unusual phrasing can be missed.
- NEXT (AGENT_PLAN step 3): Executor (wire tools: RAG, validators, price calc, capability matcher, history, drafting), then Critic, router, evaluation, UI.

## Executor (AGENT_PLAN step 3, built 2026-11-09) - 296 tests pass
- `src/lbs_rag/agent/executor.py`: `check_inputs` (coordinates incl. swapped lat/lng and provider order, matrix size with a split suggestion, waypoint limits, timestamps without timezone / relative dates, pasted-JSON shape incl. truncated bodies,
  required parameters: Ola from the OpenAPI spec, other providers honestly "no verified list"), `draft_communication` (support_reply | discovery | follow_up; templates, no model; labelled DRAFT; sources list; never promises SLA/timelines),
  `build_handoff` / `render_handoff`, `run_executor` (context -> validators -> capability match -> RAG answer -> draft -> checkpoint; stores the draft as ONE pending `external_send` approval; never sends), `tool_specs()` registry.
- `config/capabilities.yaml` + `tools/capabilities.py`: requirement -> documented capability per provider with VERBATIM quotes (8 capabilities: traffic-aware routing, route optimisation, walking/cycling, autocomplete country restriction,
  response language, matrix size, waypoint capacity, self-hosting, regional boundaries). `tests/test_capabilities.py` fails if a quote is not in the fetched page. Providers without evidence are reported as [GAP] = "not documented in the
  fetched sources; confirm with the vendor", never as "unsupported".
- Fixes made after an end-to-end run with the real RAG: plain "returns an error" is now troubleshooting (was how-to/low urgency); "10x10 request" no longer creates a fake volume fact; discovery drafts use the verified capability table instead of the
  model-written essay (which contained unsupported claims and contradicted the table).
- Known limits: drafts are templates (no model polish), the Executor is not yet wired into the UI/API, required-params check is Ola-only, capability library is small (8 capabilities) and HERE/Nominatim/OSRM gaps are mostly genuine evidence gaps in the fetched pages.
- NEXT (AGENT_PLAN step 4): the Critic (claims vs evidence via grounding, handoff completeness, capability-evidence check, policy/guard check, revision loop max 2), then router, evaluation, UI.

## Critic (AGENT_PLAN step 4, built 2026-11-09) - 312 tests pass
- `src/lbs_rag/agent/critic.py` (no model). Checks: grounding (cited sentences vs the cited passages), numbers (every number in the draft has a source: cited passages, calculator, validators, capability evidence, known facts), commitments (SLA / uptime /
  refund / discount / fix date / timeline; a negated disclaimer is fine), safety (secrets / personal data), validations (every failed validation is mentioned), capability (documented rows need a verified source; a [GAP] must never be written up as "unsupported"),
  handoff completeness (problem statement, findings, owner or proposed owner, next action, unresolved questions listed; sales needs use_case / api_needs / volume or an explicit unresolved list).
- Two separate outputs: `verdict` for the DRAFT (pass | revise | needs_human) and `handoff_ready` + `handoff_issues` for the CASE. `critique_and_revise` repairs deterministically (drops flagged sentences, re-drafts; never invents text) at most `MAX_REVISIONS` = 2 times;
  unfixable issues (e.g. personal data in a draft) go straight to needs_human. `critique_case` persists: trace step, case state, the pending `external_send` payload updated with the revised draft and the verdict, case status `needs_human` when applicable.
- Approvals tab now shows the Critic verdict, issues and handoff warning above each draft.
- Measured: whole chain (Planner -> Executor -> Critic, real RAG) on 28 development questions that are not price questions: 28 pass, 3 needed one revision, 0 issues left after revision. Small sample; the Critic only catches unsupported / unsafe / incomplete
  material, it CANNOT tell that a well-sourced-looking claim is wrong, so it does not replace human approval.
- NEXT (AGENT_PLAN step 5-7): approval flow extensions (tracker updates / handoffs), the Router (simple -> RAG, complex -> agent, human judgement -> person), an orchestrator tying Planner -> Executor -> Critic -> approvals with pause/resume,
  evaluation harness (routing accuracy, approval-gate tests, handoff completeness, Critic catch rate on seeded wrong claims, cost/latency), UI.

## Router, Orchestrator, evaluation harness, UI/API integration (built 2026-11-09) - 363 tests pass
- `agent/router.py` + `config/router.yaml`: refused | rag | agent | human, rules only, each decision carries a reason. `agent/orchestrator.py`: `handle_message`, `reply` (resume), `prepare_handoff`.
  `store/handlers.py` handles every approval type (internal updates only); approved tracker table `case_tracker`; Executor proposes tracker updates and refreshes (not duplicates) pending drafts.
- `eval/agent_eval.py` (development split): routing 97.8% on clear classes (tuned there), escalations 14/14 to agent or person, approval gates 17/17, Critic seeded-claims table (commitment/personal data/unrelated 100%, invented number 97.5%,
  wrong provider 82%, false positives 1.2%). `--chain N` runs real messages through the full chain (needs the gateway). FIRST chain run was invalid (a stub RAG leaked from the gate test); fixed, rerun was in progress when notes were written.
- Fixes found by the harness: citation marker after the full stop hid unsupported claims (grounding + Critic), lowercase "here" was treated as provider HERE (detect, grounding), URL slugs were treated as API keys, Indian phone numbers with a space were
  not redacted, Planner overwrote case state, off-topic questions could open a case. UI: Cases tab + type-aware Approvals tab. API: /cases/message, /cases/{id}/reply, /cases/{id}/handoff, /cases.
- Chain evaluation rerun (valid): 18 real non-price dev questions through the full chain: agent route mean 12.4 s, direct RAG route 5.3 s, Critic 10/10 pass, handoff ready 10/10, approvals per case mean 3.1 (max 4).
- UI confirmed working by the user. Added close/reopen (`cases.close_case` / `reopen_case`, UI sub-tab, API `/cases/{id}/close|reopen`); closed cases refuse new replies until reopened. 368 tests pass.
- Full task list: see BACKLOG.md (sections A-D).

## Roles for the next round (reiterated)
- TESTER: opens the UI (app must be started; same network, `http://<PC-address>:8501`), asks 40-60 realistic questions (prices, free tier, errors, policy/parameters, general, things that
  should be refused, HERE/other providers), rephrases some 3 ways, logs question / right-wrong-declined / sources ok? / severity; never shares keys; reports question text + time (all logged in `data/lbs.db`).
- HUMAN REVIEWER: fills `eval/review/answer_review_v2.xlsx` (Answers sheet, green "REVIEWER verdict" dropdown + notes) for the 30 rows, also checks citations and prices vs "Calculator figures",
  freely disagrees with Claude's yellow (unreviewed) column, returns the file. v1 (`answer_review.xlsx`) holds pre-fix answers.

## Next steps (in order)
1. Finish the routing-with-rewrite comparison; if it helps, set `route=True` as default (answer(), API, UI) and add a test.
2. (Done: smaller chunks rejected, routing adopted, guard + general questions added.)
3. IN PROGRESS: full 400 development run with EVERYTHING (facts, free-tier, routing, general questions, guard). Report file: newest `eval/reports/answer_eval_*.jsonl`
   (started after 2026-11-09 evening). Compare with the baseline above (251/112/37, number match 92.6%, citation support 77.0%). If it did not finish, rerun
   `python eval/answer_eval.py --n 400 --system rag`. Check that no real question was refused or escalated by mistake (look for `refused`/off_topic in the per-item results).
4. Human review of `answer_review_v2.xlsx` (your reviewer). Compare their verdicts with mine.
5. Holdout 100: run ONCE (`load_items("holdout")`), only after final changes. Do not tune against it.
6. Not built: Planner/Executor/Critic agent layer (project doc asks for it; `agent/` has stubs), case handling, action execution (approval only records a decision).
7. Housekeeping: write `requirements.txt` (missing; testers cannot install from scratch), `git commit` (nothing committed; keep `.env` out), tester guide, handover note.
8. Vendors: confirm Ola Distance Matrix free allowance (pairs vs requests; `pricing.yaml` says pairs, UNCONFIRMED) and get HERE's price (answers abstain today).

## Decisions and warnings worth remembering
- Re-ranking hurt (bge-reranker-base) and is slow on CPU: keep OFF. k=8 chunks did not beat k=6. Query rewrite helped citation support (74% -> 90% on 60 questions).
- Plain price questions are answered ENTIRELY by code; any prices the model writes are stripped. Pricing needs a real volume in the question (guard against invented workloads).
- Facts in `config/facts.yaml` are a targeted patch for rules retrieval missed (each has a verbatim quote; `tests/test_facts.py` checks quotes exist in the fetched pages). Scores on questions they were written for will rise because of the patch, not general retrieval.
- Testers: Option A = run Streamlit on your PC with `--server.address 0.0.0.0` (same network; no login, so Approvals tab is open to anyone on it). Option B = own copy (needs requirements.txt, `.env`, the certificate `vm07-ca.crt`, gateway reachability). Never send the API key in chat or commit it.
- Gateway key and certificate path live in `.env` (gitignored). Certificate: `C:\Users\Deepshikha\Downloads\vm07-ca.crt`.
