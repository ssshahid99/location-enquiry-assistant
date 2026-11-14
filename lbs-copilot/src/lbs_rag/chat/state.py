"""Conversation state store (Activity 13): known facts, questions already asked / answered, a rolling summary, the last turns.

Everything is in SQLite (data/lbs.db), so a conversation survives a restart. Pure code, no model calls.
"""
import json
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create(con, customer: str = "unknown", conv_id: str | None = None) -> str:
    cid = conv_id or "C-" + uuid.uuid4().hex[:8]
    con.execute("INSERT OR IGNORE INTO conversations (id, customer, created_at, updated_at, summary, pending_json) VALUES (?,?,?,?,?,?)",
                (cid, customer, _now(), _now(), "", None))
    con.commit()
    return cid


def exists(con, conv_id: str) -> bool:
    return con.execute("SELECT 1 FROM conversations WHERE id=?", (conv_id,)).fetchone() is not None


def add_message(con, conv_id: str, role: str, content: str, route: str | None = None) -> int:
    cur = con.execute("INSERT INTO conv_messages (conv_id, role, content, route, ts) VALUES (?,?,?,?,?)",
                      (conv_id, role, content, route, _now()))
    con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conv_id))
    con.commit()
    return cur.lastrowid


def history(con, conv_id: str, limit: int = 8) -> list[dict]:
    rows = con.execute("SELECT role, content, route, ts FROM conv_messages WHERE conv_id=? ORDER BY id DESC LIMIT ?", (conv_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def set_facts(con, conv_id: str, facts: dict[str, str]) -> list[str]:
    """Store facts; returns the keys that are new or changed."""
    cur = known_facts(con, conv_id)
    changed = []
    for k, v in facts.items():
        if v and cur.get(k) != v:
            con.execute("INSERT OR REPLACE INTO conv_facts (conv_id, key, value, ts) VALUES (?,?,?,?)", (conv_id, k, v, _now()))
            changed.append(k)
    con.commit()
    return changed


def known_facts(con, conv_id: str) -> dict[str, str]:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM conv_facts WHERE conv_id=?", (conv_id,))}


def mark_asked(con, conv_id: str, field: str, question: str) -> None:
    con.execute("INSERT OR IGNORE INTO conv_asked (conv_id, field, question, ts, answered) VALUES (?,?,?,?,0)", (conv_id, field, question, _now()))
    con.commit()


def asked(con, conv_id: str) -> dict[str, dict]:
    return {r["field"]: {"question": r["question"], "answered": bool(r["answered"])}
            for r in con.execute("SELECT field, question, answered FROM conv_asked WHERE conv_id=?", (conv_id,))}


def mark_answered(con, conv_id: str, fields) -> None:
    for f in fields:
        con.execute("UPDATE conv_asked SET answered=1 WHERE conv_id=? AND field=?", (conv_id, f))
    con.commit()


def unanswered(con, conv_id: str) -> list[str]:
    return [f for f, a in asked(con, conv_id).items() if not a["answered"]]


def set_pending(con, conv_id: str, pending: dict | None) -> None:
    """The question the customer asked before we asked for more details; answered once they reply."""
    con.execute("UPDATE conversations SET pending_json=? WHERE id=?", (json.dumps(pending) if pending else None, conv_id))
    con.commit()


def get_pending(con, conv_id: str) -> dict | None:
    r = con.execute("SELECT pending_json FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return json.loads(r["pending_json"]) if r and r["pending_json"] else None


def refresh_summary(con, conv_id: str, max_chars: int = 900) -> str:
    """Rolling summary, deterministic: known facts plus the customer's recent questions (shortened)."""
    facts = known_facts(con, conv_id)
    users = [m["content"] for m in history(con, conv_id, 40) if m["role"] == "customer"]
    parts = []
    if facts:
        parts.append("Known: " + "; ".join(f"{k}={v}" for k, v in facts.items()))
    if users:
        parts.append("Customer asked: " + " | ".join(u.replace("\n", " ")[:110] for u in users[-5:]))
    s = " ".join(parts)[:max_chars]
    con.execute("UPDATE conversations SET summary=? WHERE id=?", (s, conv_id))
    con.commit()
    return s


def load(con, conv_id: str) -> dict:
    r = con.execute("SELECT customer, summary FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return {"conv_id": conv_id, "customer": r["customer"] if r else "unknown", "summary": (r["summary"] if r else "") or "",
            "facts": known_facts(con, conv_id), "asked": asked(con, conv_id), "pending": get_pending(con, conv_id),
            "history": history(con, conv_id, 8)}
