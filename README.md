# Location Services Sales & Support Copilot

Grounded question answering and an agent workflow (Planner, Executor, Critic) over Google Maps Platform, Mapbox, HERE, Ola Maps, OSM Nominatim
and OSRM documentation (114 sources). Prices are computed by code, not by the model. **Consequential actions are only ever proposed**: one authorised
human approves (`config/approvers.yaml`), and approving applies an internal update only. **Nothing is sent externally.**

## How it works
```
message -> PII/secret redaction -> ROUTER
  refused  scope/ethics guard (off-topic, abuse, deception, prompt injection, secrets, advice ...)
  rag      guard -> escalation rules -> query rewrite -> hybrid retrieval (BM25 + embeddings, RRF, provider routing)
           -> price extraction + calculator / free-tier / facts / capability evidence -> grounded answer (Qwen3-8B) -> grounding check
  agent    case: PLANNER (classify, extract facts, checklist, <= 4 questions, escalation recommendation, owner/next-action proposal)
           -> EXECUTOR (validators, RAG answer, capability match, draft, tracker proposal) -> CRITIC (verify / repair x2 / needs human)
  human    contract / SLA and security matters: case + handoff package, no customer draft
every proposal -> pending approval (reply draft, escalation, tracker update, handoff, owner action) -> one approver -> internal update
```
Key design points: deterministic code does prices, limits, validators, routing and the Critic; the model writes language only where needed;
every fact, capability and quote is verified against the fetched pages by tests; abstention and refusal are explicit and explained.

## Setup (Windows, PowerShell)
```powershell
uv venv --python 3.12 .venv ; .\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
python -m playwright install chromium            # only needed to re-fetch the JS-rendered sources
copy .env.example .env                           # set OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_CA_BUNDLE (never commit .env)
$env:PYTHONPATH = ".;src"
python -m lbs_rag.ingest.fetch                   # fetch the 114 sources into data/raw/<date>/ (network)
python -m lbs_rag.ingest.run_ingest              # parse + chunk -> data/processed/chunks.jsonl
python -m lbs_rag.index.build_index              # embed into Chroma (~10 min on CPU); BM25 rebuilds on load
```
Chat model: `Qwen/Qwen3-8B` through the project gateway (1 concurrent request per key, no `/embeddings` endpoint); embeddings run locally
(`BAAI/bge-base-en-v1.5`). Optional person-name redaction needs `python -m spacy download en_core_web_lg`.

## Run
```powershell
$env:PYTHONPATH = ".;src"
streamlit run app/streamlit_app.py               # UI: Ask | Cases | Approvals   (http://localhost:8501)
uvicorn lbs_rag.api.server:app --port 8000       # API docs at http://localhost:8000/docs
python -m pytest tests -q                        # ~370 tests, offline (the model is stubbed)
```
API: `/answer`, `/cases/message`, `/cases/{id}/reply`, `/cases/{id}/handoff`, `/cases/{id}/close|reopen`, `/cases`, `/cases/{id}`,
`/actions/pending`, `/actions/{id}/decide`, `/approvers`, `/health`.

## Configuration (all in `config/`)
`pricing.yaml` (prices, limits, FX), `facts.yaml` and `capabilities.yaml` (verbatim-quote evidence), `escalation_rules.yaml`, `planner.yaml`
(checklists, PROPOSED escalation evidence policy), `router.yaml`, `approvers.yaml`, `sources.yaml`, `settings.py` (+ `.env`).

## Evaluation (`eval/`; the 100-question holdout is reserved for ONE final run)
| Script | Measures |
|---|---|
| `answer_eval.py` | judged correctness, headline-number match, citation support, HERE-price violations; `--no-rewrite`, `--pricing-only` |
| `chunk_recall.py` | does the cited passage itself reach the model (honest retrieval metric) |
| `agent_eval.py` | routing, approval-gate invariants, Critic on seeded wrong claims, `--chain N` real messages through the full chain |
| `planner_eval.py`, `escalation_eval.py`, `pricing_sweep.py` | classification, escalation precision/recall, calculator vs ground truth |
| `make_review_sheet.py` | human-review spreadsheets (`eval/review/`) |

## Project documents
`BACKLOG.md` (open items), `IMPLEMENTATION_PLAN.md` (plan for the must/should items), `PROJECT_CHECKLIST.md` (resume here), `AGENT_PLAN.md`,
`SESSION_NOTES.md` (history and decisions), `TESTER_GUIDE.md` + `tester_log_template.csv`.

## Known limits
HERE prices unverified; Ola Distance Matrix free allowance (pairs vs requests) unconfirmed; the approved escalation policy (SC-02) has not been supplied so
escalation rules are a proposal; passage retrieval and the small chat model limit accuracy on explanatory questions; the Critic checks support, not truth;
keyword rules in the guard can miss cleverly worded requests; the UI has no login.
