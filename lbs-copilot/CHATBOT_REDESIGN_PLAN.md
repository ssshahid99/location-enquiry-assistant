# Chatbot redesign: one Planner that routes to LLM, RAG or Escalation

Written for Deepshikha, 2026-11-14. Scope is the Activities sheet: 7-10 (RAG) and 11-17 (agent side). Activity 19 (the Planner-Executor-Critic loop) is out of scope.
Status: PLAN. Nothing below is built yet. Human approval stays mandatory for every consequential action.

## 1. What is wrong with the current agent (plain words)
The current agent is a case-management workflow: Planner (classify, checklists, proposals) then Executor (24 capabilities) then Critic (revision loop).
It does far more than the sheet asks for, and a customer cannot simply chat with it: a message first becomes a "case". The sheet asks for a chatbot-like system
with a small tool set, a conversation memory, a drafter, an escalation router and a review queue. So we keep the parts that match the sheet and retire the rest from the main path.

## 2. Target design

```
 user message
   |
 INTAKE            PII redaction, guard (off-topic / unsafe -> refuse)                     [exists]
   |
 CONVERSATION STATE  facts already known, questions already asked/answered, rolling summary  [Activity 13]
   |
 PLANNER (router)  chooses exactly ONE route, with a reason, logged on the trace
   |-- LLM         conversation only: greetings, "what can you do", follow-ups that refer to earlier turns, rewording,
   |               and the clarifying questions for missing details. No retrieval. May not state facts about prices/limits/errors.
   |-- RAG         anything that needs a fact from the documents or a number: retrieve -> (calculator if a volume is given) -> grounded draft [Activities 7-12]
   |-- ESCALATION  needs a person: ownership, severity, escalate yes/no + reason (YAML rules, LLM only if ambiguous) -> pending proposal [Activity 16]
   |
 RESPONSE DRAFTER  schema-valid JSON {answer, clarifying_questions, qualification, comparison} + rendered text   [Activity 14]
   |
 HUMAN REVIEW QUEUE  any escalation or sandbox record update is held; approve / reject / edit; decision logged     [Activities 15, 17]
```

The Planner picks a route per message, not a long plan. One route per turn keeps latency and cost low (one extra model call at most, and none when a rule decides).

## 3. How the Planner decides (rules first, model only when unclear)
1. Guard says refuse -> reply with the refusal (no route).
2. Escalation rules fire (production incident, security, billing dispute, legal, "talk to a person") -> ESCALATION. Safety first: a rule match is never overridden by the model.
3. Greeting / capability question / pure follow-up with no factual ask (rules from `meta.py` plus the conversation state) -> LLM.
4. Mentions a provider, API, price, limit, error, parameter, volume -> RAG.
5. Still unclear -> one small JSON call: {"route": "llm|rag|escalation", "reason": "..."}; validated by code; any invalid output falls back to RAG (the safest route, because it is grounded and cited).

Gold labels for checking it exist in ground truth v3: `should_escalate` (20 of 481 are escalations), `expected_tools` (doc_search, calculator, update_record, escalate_ticket) and `gold_clarifying_questions` (114 items).
Route accuracy can be scored with no new labelling: escalation = should_escalate; RAG = doc_search or calculator expected; LLM = no tool expected and a clarifying question expected.

## 4. Activity by activity

| # | Activity | What exists | What to build / change |
|---|---|---|---|
| 7 | Corpus (500-2,000 docs, metadata, near-duplicates <2%) | 122 sources, 1,417 chunks, metadata stored | Add licence field; MinHash near-duplicate report |
| 8 | Chunk sizes + BM25 and dense indexes, one rebuild script | Token-exact chunker (480/512), BM25 + dense, endpoint embeddings | Report recall@5 for 2+ chunk sizes on the dev split; single `rebuild_index` script |
| 9 | Hybrid + reranker, 4 variants reported | Hybrid RRF; reranker implemented but OFF (it hurt) | Re-measure the 4 variants on the new chunks and report them as the sheet requires (recall@5, nDCG@10) |
| 10 | Citations + retrieval metrics (recall@1/5/10, MRR, per bucket; >=95% cite; precision >=0.85) | Citation grounding, strict span recall | Add MRR and per-bucket report; citation precision (now 77% by our checker) is below the 0.85 target |
| 11 | Deterministic comparison/calculator, >=30 hand-calculated tests, byte-identical output, assumptions + sources | Pricing calculator, rate card, free-tier | Add 30 hand-calculated cases; make output carry assumptions and source docs |
| 12 | Tool registry, >=3 typed tools, function calling, schema-validated calls, 0 unregistered calls | Tools exist as plain functions | Registry with Pydantic in/out for `doc_search`, `compare_calculate`, `sandbox_update`; log every call |
| 13 | Conversation state (facts, asked/answered, summary; survives restart) | Case history and known facts in SQLite | Move to a per-conversation state table; "do not re-ask" check; restart test |
| 14 | Response drafter, schema-valid JSON + text | Free-text answers | JSON schema (answer, clarifying questions, qualification, comparison) and a renderer; schemas come from Shahid's repo (Activity 1) |
| 15 | Sandbox store; update tool writes only a field-level diff, nothing applied | Cases/customers tables, proposed actions | Mock CRM/ticket tables; `sandbox_update` returns a diff only; test that non-sandbox writes are impossible |
| 16 | Ownership + escalation router (YAML rules, LLM for ambiguous) | Escalation rules + approved policy SC-02 (severity, team) | Wrap as the ESCALATION route; report owner accuracy >=90%, escalation recall >=0.90, precision >=0.80 |
| 17 | Review queue: approve / reject / edit; decision logged with reviewer, time, final diff | Approvals tab (approve/reject, single approver) | Add "edit"; log final diff; 20-action end-to-end test |

## 5. What happens to the existing agent code
Kept and reused: `answer()` (the RAG route), pricing tools, `escalation.py` + `escalation_policy.py`, PII, guard, `meta.py`, case/customer/approval stores, the Streamlit Approvals tab, tracing.
Moved off the main path (not deleted, in the backlog): the Executor's 24-capability library, the Critic revision loop, and the case-first workflow.
The grounding check stays inside the RAG route, so answers are still checked; it is not a separate Critic stage.

## 6. Build order (each step testable offline first)
1. Planner router: rules + JSON fallback + trace of the chosen route; test on the gold labels (no gateway needed for the rule part).
2. Chat entry point `chat(message, conversation_id)` that calls the route and the drafter; Streamlit chat uses it.
3. Conversation state table and "do not re-ask".
4. Tool registry with typed tools; sandbox store and diff-only update tool.
5. Response drafter JSON schema.
6. Review queue: add edit and decision log.
7. Evaluate: route accuracy, escalation recall/precision, owner accuracy, state tests, schema validity.

Estimated effort: about 1.5 to 2 days of work; gateway time only for the model-based parts of steps 1 and 7 (roughly 20 minutes).

## 7. Decisions needed
- **What the LLM route may say.** Proposed: conversation only (greetings, follow-ups, clarifying questions, rewording), never facts about prices, limits or errors. Alternative: allow general knowledge answers; this risks ungrounded claims.
- **Where qualification questions come from.** Proposed: the LLM route, using `gold_clarifying_questions` style and the state store.
- **Shahid's schemas (Activities 1-2).** The response drafter should use his JSON Schemas from the Track-B repo, so we need them as the contract.

## 8. Status (2026-11-14, end of day): built and running
Built: `src/lbs_rag/chat/` (planner, state, registry, sandbox, drafter, chat), `schemas/` (Shahid's final schemas, copied), `eval/chat_eval.py`, a Chat tab in the UI,
a `record_update` approval type (editable fields, applied to the sandbox table only after approval). 568 tests pass (28 new for the chat system).
Measured on the working pool (378 items; rules were tuned here, so these are optimistic): route accuracy 99.7%, escalation recall 1.00 / precision 0.91, owner accuracy 88.1%.
Measured once on the core scoreboard (not tuned on): route accuracy 93.8%, escalation recall 0.50 (5 of 10 missed), precision 1.00, owner accuracy 68.8%. The escalation rules do not generalise
to unseen wording, and the model fallback did not fix it (it added false escalations, so it is OFF: LBS_ESC_MODEL=1 to try again). Needs more labelled escalation examples (only 20 exist).
Not done yet: Activities 7-10 reports (MinHash near-duplicates, 2+ chunk sizes recall@5, four-variant retrieval table, MRR per bucket), 30 hand-calculated calculator cases,
state "do not re-ask" scoring on the history bucket, 20-action review-queue end-to-end test, API server endpoint for chat.
