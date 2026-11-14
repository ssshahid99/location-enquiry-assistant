"""The Streamlit app renders and the Chat tab works end to end for turns that need no model (greeting, request for a person). Temp database."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from lbs_rag.store import cases  # noqa: E402
from lbs_rag.store import repositories as repo  # noqa: E402
from lbs_rag.store.db import connect  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    db = tmp_path / "ui.db"
    monkeypatch.setattr(repo, "connect", lambda: connect(db))
    cases.reload_approvers()
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"), default_timeout=60)
    at.run()
    return at, db


def test_app_renders_without_errors(app):
    at, _ = app
    assert not at.exception
    assert [t.label for t in at.tabs][:5] == ["Chat", "Direct answer", "Case workflow (older)", "Approvals", "Traces"]


def test_chat_greeting_then_request_for_a_person(app):
    at, db = app
    at.chat_input[0].set_value("Hello").run()
    assert not at.exception
    assert any("LLM" in c.value for c in at.caption)
    at.chat_input[0].set_value("I want to talk to a human agent please").run()
    assert not at.exception
    assert any("Escalation" in c.value for c in at.caption)
    assert any("Waiting for human approval" in w.value for w in at.warning)
    con = connect(db)
    pend = cases.list_pending(con)
    con.close()
    assert len(pend) == 1 and pend[0]["type"] == "escalation" and pend[0]["status"] == "pending"
