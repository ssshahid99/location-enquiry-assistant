import time

import pytest

from lbs_rag.index.bm25 import load_chunks
from lbs_rag.store import cases, traces
from lbs_rag.store import repositories as repo
from lbs_rag.store.db import connect


@pytest.fixture()
def con(tmp_path):
    return connect(tmp_path / "t.db")


@pytest.fixture(scope="module")
def two_chunks():
    cs = [c for c in load_chunks() if c["kind"] != "stackoverflow"][:2]
    return cs


def _save(con, tid, query, chunks, *, reason=None, abstained=False, escalate=False, cited=0):
    a = {"text": f"answer to {query}", "abstained": abstained, "escalate": escalate, "reason": reason,
         "retrieved": [{"chunk_id": c["chunk_id"], "doc_url": c["doc_url"]} for c in chunks], "tool_calls": [{"tool": "pricing_calc", "output": []}],
         "citations": [{"n": i + 1, "chunk_id": c["chunk_id"], "doc_url": c["doc_url"], "provider": c["provider"]} for i, c in enumerate(chunks[:cited])]}
    repo.save_trace(con, trace_id=tid, query=query, a=a, model="Qwen/Qwen3-8B")


def test_list_filters_and_search(con, two_chunks):
    _save(con, "t1", "ola price question", two_chunks)
    _save(con, "t2", "what is the capital of France", [], reason="refused:off_topic", abstained=True)
    _save(con, "t3", "claim with no support", two_chunks, reason="ungrounded", abstained=True)
    _save(con, "t4", "outage on ola", two_chunks, reason="production_incident", escalate=True)
    ids = lambda **k: {t["id"] for t in traces.list_traces(con, **k)}
    assert ids() == {"t1", "t2", "t3", "t4"} and ids(only="refused") == {"t2"} and ids(only="ungrounded") == {"t3"}
    assert ids(only="abstained") == {"t2", "t3"} and ids(only="escalated") == {"t4"} and ids(query_contains="ola") == {"t1", "t4"}
    with pytest.raises(ValueError):
        traces.list_traces(con, only="bogus")


def test_get_trace_shows_passages_and_which_were_cited(con, two_chunks):
    _save(con, "t1", "ola price question", two_chunks, cited=1)
    t = traces.get_trace(con, "t1")
    assert [p["rank"] for p in t["passages"]] == [1, 2] and [p["cited"] for p in t["passages"]] == [True, False]
    assert t["passages"][0]["provider"] == two_chunks[0]["provider"] and t["passages"][0]["snippet"]
    assert t["tool_calls"][0]["tool"] == "pricing_calc" and t["answer"].startswith("answer to")
    assert traces.get_trace(con, "nope") is None


def test_missing_passages_are_reported_not_hidden(con):
    repo.save_trace(con, trace_id="t9", query="q", a={"text": "x", "abstained": False, "escalate": False,
                                                      "retrieved": [{"chunk_id": "gone", "doc_url": "https://x"}]}, model="m")
    assert "no longer in the corpus" in traces.get_trace(con, "t9")["passages"][0]["snippet"]


def test_model_calls_are_written_after_the_answer_is_complete(tmp_path, monkeypatch, two_chunks):
    db = tmp_path / "m.db"
    monkeypatch.setattr(repo, "connect", lambda: connect(db))
    c = connect(db)
    _save(c, "t5", "q", two_chunks)
    repo.set_trace_model_calls("t5", 3)
    assert traces.get_trace(c, "t5")["model_calls"] == 3 and traces.list_traces(c)[0]["model_calls"] == 3


def test_case_trace_groups_steps_by_message_with_events_and_approvals(con):
    cid = cases.create_case(con, workflow="support")
    t0 = time.time()
    cases.trace_step(con, "m1", "classify", "planner", {"x": 1}, {"workflow": "support"}, t0, case_id=cid)
    cases.trace_step(con, "m1", "rag_answer", "executor", {}, {"summary": "ok"}, t0, case_id=cid)
    cases.trace_step(con, "m2", "classify", "planner", {}, {}, t0, case_id=cid)
    cases.request_approval(con, cid, "external_send", {"draft": "x"})
    ct = traces.case_trace(con, cid)
    assert set(ct["by_message"]) == {"m1", "m2"} and len(ct["by_message"]["m1"]) == 2 and ct["total_step_ms"] >= 0
    assert [a["type"] for a in ct["approvals"]] == ["external_send"] and "approval_requested" in {e["event"] for e in ct["events"]}
    t = traces.get_trace(con, "m1")                                          # a step-only trace is still viewable and linked to its case
    assert t["case_id"] == cid and len(t["steps"]) == 2


def test_export_is_one_json_bundle_with_full_passage_text(con, two_chunks):
    import json
    cid = cases.create_case(con)
    _save(con, "t6", "q", two_chunks, cited=2)
    con.execute("UPDATE traces SET case_id=? WHERE id='t6'", (cid,))
    con.commit()
    b = traces.export_trace(con, "t6")
    assert b["passages_full_text"][0]["text"] == two_chunks[0]["text"] and b["case_trace"]["case_id"] == cid
    json.dumps(b)                                                            # must be serialisable as is
    with pytest.raises(ValueError):
        traces.export_trace(con, "nope")
