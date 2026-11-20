"""Runtime few-shot loader. Git holds record IDs only (``fewshot_ids.json``).

Purpose: turn the tracked IDs into user/assistant example pairs at runtime.
Question text comes from the local GT CSV named by ``TRACKB_GT_CSV``; labels and
qualification values come from the tracked gold file (``TRACKB_FEWSHOT_GOLD``,
default ``mapping/gold_outputs_all.jsonl``). Few-shot is required. A missing or
unreadable CSV, a missing gold file, or any listed id that cannot be loaded
raises ``FewshotLoadError``. The only zero-shot path is ``TRACKB_FEWSHOT=off``
(also ``0`` or ``false``, case-insensitive), which loads nothing and logs one
INFO line. An ID that is frozen_core, holdout, or from a frozen template family
raises ``FewshotPoolError`` (pool rule, design §7).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("copilot.interpret")

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
IDS_PATH = _HERE / "fewshot_ids.json"
ENV_CSV = "TRACKB_GT_CSV"
ENV_GOLD = "TRACKB_FEWSHOT_GOLD"
ENV_MODE = "TRACKB_FEWSHOT"
_OFF_VALUES = frozenset({"off", "0", "false"})
_TAG = re.compile(r"^\s*\[(user|assistant)\]\s*", re.I)
_FIELDS = ("use_case", "region", "monthly_volume", "current_provider", "timeline",
           "budget", "technical_constraints", "decision_maker")


class FewshotPoolError(RuntimeError):
    """A listed ID is outside the allowed pool. Never silently ignored."""


class FewshotLoadError(RuntimeError):
    """Few-shot examples are required and could not be loaded."""


def fewshot_disabled() -> bool:
    """True only for an explicit ``TRACKB_FEWSHOT`` opt-out."""
    flag = os.environ.get(ENV_MODE)
    return flag is not None and flag.strip().lower() in _OFF_VALUES


def clear_fewshot_cache() -> None:
    """Drop cached loads. Tests call this when env or fixtures change."""
    _load.cache_clear()
    _load_off.cache_clear()


def fewshot_ids(path: Path = IDS_PATH) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("record_ids", [])
    return [i for i in data if isinstance(i, str)] if isinstance(data, list) else []


def split_question(q: str) -> tuple[str, list[dict]]:
    """Same split as the GT adapter: last [user] turn is the query, earlier turns history."""
    if not any(_TAG.match(line) for line in q.splitlines()):
        return q.strip(), []
    turns: list[dict] = []
    for line in q.splitlines():
        m = _TAG.match(line)
        if m:
            turns.append({"role": m.group(1).lower(), "text": line[m.end():]})
        elif turns:
            turns[-1]["text"] += "\n" + line
        else:
            turns.append({"role": "user", "text": line})
    turns = [dict(t, text=t["text"].strip()) for t in turns if t["text"].strip()]
    last = max(i for i, t in enumerate(turns) if t["role"] == "user")
    return turns[last]["text"], turns[:last]


def _evidence(value, user_text: str):
    """Exact span for the example's evidence, or None when the value is not verbatim."""
    if value is None:
        return None
    if isinstance(value, dict):  # monthly_volume
        value = value.get("raw")
    if not isinstance(value, str):
        return None
    i = user_text.lower().find(value.lower())
    return user_text[i:i + len(value)] if i >= 0 else None


def _target(gold: dict, user_text: str) -> str:
    q = gold["outputs"]["qualification_details"]
    qual = {}
    for f in _FIELDS:
        raw = (q.get("use_case_api") or {}).get("use_case") if f == "use_case" else q.get(f)
        ev = _evidence(raw, user_text)
        val = (raw.get("raw") if isinstance(raw, dict) else raw) if ev else None
        qual[f] = {"value": val, "evidence": ev}
    obj = {"intent": gold["intent"], "sub_intent": gold["sub_intent"], "api": gold["api"],
           "team": gold["team"], "confidence": {"intent": None, "api": None, "team": None},
           "qualification": qual}
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _read_text(path: Path, env_name: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FewshotLoadError(
            f"few-shot: {env_name} file does not exist or cannot be read: {path}"
        ) from exc


def _require_file(path_value: str | None, env_name: str) -> Path:
    if not path_value:
        raise FewshotLoadError(
            f"few-shot: {env_name} is unset (path=<unset>)"
        )
    path = Path(path_value)
    if not path.is_file():
        raise FewshotLoadError(
            f"few-shot: {env_name} file does not exist or cannot be read: {path}"
        )
    return path


@lru_cache(maxsize=4)
def _load_off(flag: str) -> tuple[dict, ...]:
    logger.info("few-shot is disabled by TRACKB_FEWSHOT")
    return ()


@lru_cache(maxsize=4)
def _load(ids: tuple[str, ...], csv_path: str | None, gold_path: str) -> tuple[dict, ...]:
    if not ids:
        return ()
    csv_file = _require_file(csv_path, ENV_CSV)
    gold_file = _require_file(gold_path, ENV_GOLD)
    gold = {}
    for line in _read_text(gold_file, ENV_GOLD).splitlines():
        if line.strip():
            rec = json.loads(line)
            gold[rec["record_id"]] = rec
    want = set(ids)
    rows, frozen_fams = {}, set()
    reader = csv.DictReader(io.StringIO(_read_text(csv_file, ENV_CSV)))
    for r in reader:
        if str(r.get("frozen_core", "")).strip().lower() in ("true", "1", "yes"):
            frozen_fams.add(r.get("template_family"))
        if r.get("id") in want:
            rows[r["id"]] = {"question": r["question"], "template_family": r.get("template_family")}
    out = []
    for rid in ids:
        g, row = gold.get(rid), rows.get(rid)
        if g is None or row is None:
            raise FewshotLoadError(
                f"few-shot: {rid} is missing from {ENV_CSV} ({csv_file}) "
                f"or {ENV_GOLD} ({gold_file})"
            )
        if g.get("frozen_core") or g.get("split") != "eval" or row["template_family"] in frozen_fams:
            raise FewshotPoolError(f"{rid} is outside the design §7 few-shot pool")
        query, history = split_question(row["question"])
        user_text = "\n".join([t["text"] for t in history if t["role"] == "user"] + [query])
        out.append({"record_id": rid, "query": query, "history": history, "target": _target(g, user_text)})
    if len(out) != len(ids):
        raise FewshotLoadError(
            f"few-shot: loaded {len(out)} of {len(ids)} ids from {ENV_CSV}={csv_file}; "
            "every id in fewshot_ids.json is required"
        )
    logger.info("few-shot: loaded %d/%d examples", len(out), len(ids))
    return tuple(out)


def load_examples() -> list[dict]:
    """Return every listed example, or none when few-shot is explicitly disabled."""
    if fewshot_disabled():
        flag = os.environ.get(ENV_MODE, "").strip().lower()
        return list(_load_off(flag))
    gold = os.environ.get(ENV_GOLD) or str(_REPO / "mapping" / "gold_outputs_all.jsonl")
    return list(_load(tuple(fewshot_ids()), os.environ.get(ENV_CSV), gold))


def fewshot_status() -> dict:
    """Load examples and report whether few-shot is on, plus how many loaded.

    Raises ``FewshotLoadError`` or ``FewshotPoolError`` when few-shot is required
    and the files or ids are not usable. The interpretation trace schema rejects
    extra keys, so batch runs store this on ``run_meta.json`` instead.
    """
    disabled = fewshot_disabled()
    loaded = load_examples()
    return {
        "fewshot_mode": "off" if disabled else "on",
        "fewshot_loaded": len(loaded),
    }
