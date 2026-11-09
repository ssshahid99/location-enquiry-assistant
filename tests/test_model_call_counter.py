import httpx

from config.settings import EVENT_HOOKS, count_model_calls


def _client(status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json={"ok": True})), event_hooks=EVENT_HOOKS)


def test_counts_only_successful_model_requests():
    ok, bad = _client(200), _client(429)
    with count_model_calls() as c:
        ok.post("https://gw/v1/chat/completions", json={})
        ok.post("https://gw/v1/chat/completions", json={})
        ok.post("https://gw/v1/embeddings", json={})
        ok.get("https://gw/v1/models")                      # not a model call
        bad.post("https://gw/v1/chat/completions", json={})  # a 429 that the SDK would retry: not counted
    assert c.count == 3


def test_nested_counters_both_see_every_call_and_nothing_counts_outside():
    ok = _client()
    ok.post("https://gw/v1/chat/completions", json={})       # outside any counter: ignored
    with count_model_calls() as outer:
        ok.post("https://gw/v1/chat/completions", json={})
        with count_model_calls() as inner:
            ok.post("https://gw/v1/chat/completions", json={})
            ok.post("https://gw/v1/chat/completions", json={})
        ok.post("https://gw/v1/chat/completions", json={})
    assert inner.count == 2 and outer.count == 4


def test_answer_and_orchestrator_report_model_calls(tmp_path, monkeypatch):
    """The wrappers add `model_calls` to their results (0 when no model is used)."""
    from lbs_rag.agent import orchestrator as O
    from lbs_rag.generate.answer import answer
    from lbs_rag.store.db import connect
    assert answer("hello", persist=False)["model_calls"] == 0          # general question: code only
    con = connect(tmp_path / "m.db")
    r = O.handle_message(con, "We want Ola autocomplete for 50M requests a month with an SLA and India-only data residency.")
    assert r["route"] == "human" and r["model_calls"] == 0               # contract request: code only, no model call
