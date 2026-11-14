# Agentic workflow plan (Planner / Executor / Critic) - drafted 2026-11-09

Source: `capstone-project-location-services-copilot-doc.pdf` (14 problems, minutes of 3 Nov 2026, approval boundaries). Status: PLAN, nothing below the
"Already built" section exists yet. Human approval is mandatory for operational updates, escalations, external sends and other consequential actions.

## Status (updated 2026-11-09)
Step 1 DONE: case state, history/known facts, approvals (single approver from `config/approvers.yaml`), pause/resume checkpoint, trace steps.
Step 2 DONE: Planner (classify, fact extraction, checklists, questions, escalation recommendation, proposals). Workflow accuracy 98.0% on the development split (tuned there; unproven on the holdout).
Step 3 DONE: Executor (validators, capability matcher with verified quotes, drafting, handoff, tool registry, pending external_send approval).
Step 4 DONE: Critic (grounding, numbers, commitments, safety, validations, capability evidence, handoff completeness; revision loop max 2; separate draft verdict and handoff-ready flag).
Next: step 5 approval flow extensions + orchestrator (pause/resume), step 6 router + evaluation, step 7 UI.

## Already built (the RAG is the Executor's knowledge tool)
`answer()` / `retrieve()` (cited, grounded, guarded), price calculator, free-tier check, validators (coordinates, matrix size, waypoints), request splitter,
escalation rules, PII redaction, facts, SQLite traces, `proposed_actions` approval table, Streamlit UI with an Approvals tab.

## Workflow (LangGraph state machine, deterministic-first)

```
 message --> INTAKE (PII redaction, guard, language) --> ROUTER
 ROUTER:  simple, answerable from docs ------------------> RAG answer (existing answer()) ----------------> reply
          human judgement / blocked by guard ------------> HUMAN QUEUE (proposed action) -----------------> person
          complex / multi-step / case work -------------> AGENT FLOW:

 AGENT FLOW
   PLANNER.classify   workflow (sales | support), service/API, issue category, urgency          [problems 11, 13]
   EXECUTOR.context   case history + summary + KNOWN FACTS (do not re-ask)                     [problems 4, 7]
   PLANNER.gaps       missing-information checklist for this workflow; targeted questions     [problems 1, 9]
        |-- missing critical info --> ASK customer / colleague (state saved, wait for reply)
   EXECUTOR.work      tools: retrieve/answer, validators, pricing calc, capability matcher     [problems 2, 8, 12]
   CRITIC.review      every claim vs evidence (grounding), validator results, policy, completeness   [problems 3, 7, 8]
        |-- fails --> back to EXECUTOR (max 2 revisions) --> else mark "needs human"
   EXECUTOR.draft     reply / discovery questions / pitch / follow-up using requirements + capabilities + history   [problem 10]
   PLANNER.propose    owner, status, next action, tracker updates, handoff package, escalation recommendation   [problems 5, 6, 7, 13]
   --> every proposal is a PENDING proposed_action --> HUMAN approves / rejects (name recorded)
   --> only after approval: APPLY internal updates (tracker, case status). External sends stay DRAFTS that a person sends.
   TRACE every step (sources, tool inputs/outputs, decisions, timing) --> SQLite                [problem 14]
```

## Components
| Component | Does | Uses a model? |
|---|---|---|
| Router | Chooses simple / agent / human (the minutes' cost-accuracy consideration). Rules first (guard, escalation categories, question shape), cheap model check only when unclear | Rarely |
| Planner | Classifies issue and workflow; builds the missing-information checklist; applies the escalation policy; proposes owner and next action | Small, with JSON output validated by code |
| Executor | Runs tools, retrieves history and knowledge, validates inputs, matches capabilities, drafts replies and handoffs | Drafting only |
| Critic | Checks claims against evidence, checks handoffs for requirements / findings / open questions, verifies capability evidence and flags gaps, blocks unsafe or unsupported content | Mostly code (grounding, rules); model only for completeness |
| Human | Approves operational updates, escalations, external sends and any other consequential action | n/a |

## Problem -> component -> tool
| # | Problem | Component | Tool(s) |
|---|---|---|---|
| 1 | Incomplete customer information | Planner | `gap_checklist` (YAML per workflow), `ask_questions` |
| 2 | Repeated documentation searches | Executor | `rag_answer`, `retrieve` (exists) |
| 3 | Inconsistent / unsupported responses | Critic | `grounding.verify` (exists), claim-vs-evidence check |
| 4 | Fragmented history | Executor | `get_case_history`, `summarize_history`, `known_facts` |
| 5 | Unclear ownership / next action | Planner | `propose_owner_action` (-> approval) |
| 6 | Outdated operational records | Executor | `extract_changes`, `propose_tracker_update` (-> approval) |
| 7 | Incomplete handoffs | Executor + Critic | `build_handoff`, `check_handoff` |
| 8 | Manual capability matching | Executor + Critic | `capability_matcher` (requirement -> documented capability + citation), Critic flags gaps |
| 9 | Incomplete qualification | Planner | `qualification_checklist` (budget, timeline, constraints, decision-maker, volume, regions) |
| 10 | Slow communication prep | Executor | `draft_communication` (grounded in requirements, capabilities, history) |
| 11 | Manual issue classification | Planner | `classify_issue` (rules + model) |
| 12 | Repetitive diagnostic checks | Executor | validators (exists) + `required_params_check`, `timestamp_check`, `response_shape_check` |
| 13 | Inconsistent escalation | Planner | `escalation_policy` (rules exist; APPROVED policy source still unknown, see open items), `escalation_recommendation` (-> approval) |
| 14 | Limited traceability | all | `trace_step` (new `trace_steps` table), existing `traces` |

## New tools to build
`get_case_history`, `summarize_history`, `known_facts`, `classify_issue`, `gap_checklist`, `qualification_checklist`, `capability_matcher`,
`required_params_check`, `timestamp_check`, `response_shape_check`, `build_handoff`, `check_handoff`, `draft_communication`, `extract_changes`,
`propose_*` (owner/action, tracker update, escalation, external send), `apply_approved_action`, `trace_step`.

## State saved per case (SQLite; `cases` and `messages` tables already exist)
case id, workflow (sales/support), customer, provider/API, category, urgency, owner, status, next action, known facts (key/value with source message),
open questions, requirements, findings, attempts, handoff package, pending / decided actions, trace id list.

## Approval gates (all go through `proposed_actions`; a blank approver is rejected)
operational updates (tracker, status, owner), escalations, external sends (drafts only), anything consequential. Nothing is sent or applied by the agent itself.

## Design decisions
- Deterministic orchestration; the model only where language is needed. A weak Planner or Critic compounds errors, and the gateway runs one request at a time (each model step is slow).
- Planner and Critic output is structured JSON validated by code; invalid output falls back to a safe default (ask a human).
- The Critic is mostly code: grounding check, guard rules, calculator cross-check, checklist completeness.
- Per-step timeouts and a maximum of 2 revisions; anything unresolved becomes "needs human".
- Reuse the same trace and approval tables so the Streamlit Approvals tab keeps working.

## Evaluation (matches the minutes: routing, validators, approval gates, handoffs, retrieval faithfulness, end-to-end grounding)
Routing accuracy vs labelled intents; escalation precision/recall (exists); validator unit tests; approval-gate tests (nothing applies without approval, blank approver rejected);
handoff completeness checklist score; Critic catch rate on seeded wrong claims; end-to-end: grounded-answer rate, citation support, abstention behaviour; cost per case (model calls) and latency.

## Build order
1. State + case history tools + `trace_steps` (foundation, no model).
2. Planner: classify + gap/qualification checklists (rules first).
3. Executor wiring to existing tools + new validators.
4. Critic (grounding, handoff completeness, capability evidence).
5. Approval flow for tracker updates / handoffs / escalations (extend the Approvals tab).
6. Router + evaluation harness; 7. UI integration; 8. end-to-end tests and the routing/approval evaluation.

## Open items from the project document (need an owner, not code)
- Approved escalation policy source (SC-02) - our rules are a proposal until this exists.
- Knowledge base / documentation source for problems 2 and 8 (SC-03) - we built it from the ground-truth links.
- Confirmation of the human-approval points; technology stack, repository and model provider are still "TBD" in the document.
- Baseline evaluation metrics were still in progress with another team member (standalone LLM baseline); ours is `eval/baseline_llm.py`.
