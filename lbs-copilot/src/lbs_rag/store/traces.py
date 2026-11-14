"""Trace viewer helpers (project problem 14: "instrumentation logs retrieved sources, tool results and execution steps for review and
failure investigation"). Read-only: nothing here changes any record.

An answer trace = the `traces` row (query, answer, reason, flags, measured model calls, retrieved passages, citations, tool results).
A case trace = the ordered `trace_steps` of every Planner / Executor / Critic step plus the case events and approvals.
`export_trace` bundles everything, with the retrieved passage text, into one JSON-serialisable dict for failure investigation.
"""
import json
from functools import lru_cache

FILTERS = {"abstained": "abstained=1", "escalated": "escalate=1", "refused": "reason LIKE 'refused%'", "ungrounded": "reason='ungrounded'",
           "no_answer": "reason='no_answer'", "general": "reason LIKE 'general%'"}


@lru_cache(maxsize=1)
def _chunk_index() -> dict[str, dict]:
    from ..index.bm25 import load_chunks
    return {c["chunk_id"]: c for c in load_chunks()}


def _parse(row) -> dict:
    d = dict(row)
    for k in ("retrieved_json", "tool_calls_json", "citations_json"):
        try:
            d[k.replace("_json", "")] = json.loads(d[k]) if d.get(k) else []
        except ValueError:
            d[k.replace("_json", "")] = []
    return d


def list_traces(con, *, only: str | None = None, limit: int = 50, query_contains: str | None = None) -> list[dict]:
    """Newest first. `only` is one of FILTERS (abstained, escalated, refused, ungrounded, no_answer, general)."""
    where, args = [], []
    if only:
        if only not in FILTERS:
            raise ValueError(f"unknown filter '{only}' (choose from {', '.join(FILTERS)})")
        where.append(FILTERS[only])
    if query_contains:
        where.append("query LIKE ?")
        args.append(f"%{query_contains}%")
    sql = "SELECT id, ts, query, reason, abstained, escalate, model_calls, case_id FROM traces"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [dict(r) for r in con.execute(sql + " ORDER BY ts DESC LIMIT ?", (*args, limit))]


def retrieved_passages(retrieved: list[dict], citations: list[dict] | None = None, snippet: int = 400) -> list[dict]:
    """The passages the model was shown, in rank order, with whether each one was cited. Chunk ids not in the corpus (corpus rebuilt since)
    are reported as missing instead of hidden."""
    idx, cited = _chunk_index(), {c.get("chunk_id") for c in (citations or [])}
    out = []
    for rank, r in enumerate(retrieved, 1):
        c = idx.get(r.get("chunk_id"))
        out.append({"rank": rank, "chunk_id": r.get("chunk_id"), "doc_url": r.get("doc_url"), "cited": r.get("chunk_id") in cited,
                    "provider": c["provider"] if c else None, "section": c["section"] if c else None,
                    "snippet": (c["text"][:snippet] if c else "(passage no longer in the corpus)")})
    return out


def get_trace(con, trace_id: str) -> dict | None:
    r = con.execute("SELECT * FROM traces WHERE id=?", (trace_id,)).fetchone()
    steps = [dict(s) for s in con.execute("SELECT id, case_id, step, component, input_json, output_json, ms, ts FROM trace_steps "
                                          "WHERE trace_id=? ORDER BY id", (trace_id,))]
    if r is None and not steps:
        return None
    d = _parse(r) if r is not None else {"id": trace_id, "retrieved": [], "tool_calls": [], "citations": []}
    d["steps"] = steps
    d["case_id"] = d.get("case_id") or next((s["case_id"] for s in steps if s["case_id"]), None)
    d["passages"] = retrieved_passages(d["retrieved"], d["citations"])
    return d


def case_trace(con, case_id: str) -> dict:
    """Everything that happened on a case, in order: steps (grouped by message), events, approvals."""
    steps = [dict(s) for s in con.execute("SELECT id, trace_id, step, component, input_json, output_json, ms, ts FROM trace_steps "
                                          "WHERE case_id=? ORDER BY id", (case_id,))]
    events = [dict(e) for e in con.execute("SELECT ts, actor, event, detail_json FROM case_events WHERE case_id=? ORDER BY id", (case_id,))]
    approvals = [dict(a) for a in con.execute("SELECT id, type, status, requested_by, approver, decision_note, created_at, decided_at, applied_at "
                                              "FROM proposed_actions WHERE case_id=? ORDER BY id", (case_id,))]
    by_trace: dict[str, list] = {}
    for s in steps:
        by_trace.setdefault(s["trace_id"], []).append(s)
    return {"case_id": case_id, "steps": steps, "by_message": by_trace, "events": events, "approvals": approvals,
            "total_step_ms": sum(s["ms"] or 0 for s in steps)}


def export_trace(con, trace_id: str) -> dict:
    """One JSON-serialisable bundle for failure investigation (answer, flags, passages with text, tool results, steps)."""
    t = get_trace(con, trace_id)
    if t is None:
        raise ValueError(f"trace {trace_id} not found")
    idx = _chunk_index()
    t["passages_full_text"] = [{"rank": p["rank"], "chunk_id": p["chunk_id"], "doc_url": p["doc_url"], "cited": p["cited"],
                                "text": idx[p["chunk_id"]]["text"] if p["chunk_id"] in idx else None} for p in t["passages"]]
    if t.get("case_id"):
        t["case_trace"] = case_trace(con, t["case_id"])
    return t
