"""SQLite connection (traces, cases, messages, approvals, case events, facts). File: data/lbs.db (gitignored)."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "lbs.db"
SCHEMA = Path(__file__).with_name("schema.sql")

# Columns added after the first version of the schema. CREATE TABLE IF NOT EXISTS never alters an existing table, so an older
# data/lbs.db is upgraded here (additive only; no data is touched).
_ADDED_COLUMNS = {
    "cases": {"type": "TEXT", "workflow": "TEXT", "provider": "TEXT", "api": "TEXT", "category": "TEXT", "urgency": "TEXT",
              "next_action": "TEXT", "state_json": "TEXT", "updated_at": "TEXT"},
    "proposed_actions": {"requested_by": "TEXT", "decision_note": "TEXT", "edited_payload_json": "TEXT", "applied_at": "TEXT",
                         "applied_result": "TEXT"},
}


def _migrate(con: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, typ in cols.items():
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    con.commit()


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    _migrate(con)
    return con
