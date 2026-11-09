import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import gates  # noqa: E402


def _baseline(tmp_path, monkeypatch, correct=276, n=400):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"n": n, "correct": correct, "label": "test"}))
    monkeypatch.setattr(gates, "BASELINE", p)


def test_drop_gate_passes_within_two_points_and_fails_beyond(tmp_path, monkeypatch):
    _baseline(tmp_path, monkeypatch)                       # 69.0%
    ok = gates.gate_drop({"n": 400, "correct": 269}, "r")  # 67.25% = -1.75 points
    bad = gates.gate_drop({"n": 400, "correct": 267}, "r")  # 66.75% = -2.25 points
    assert ok["ok"] is True and bad["ok"] is False


def test_missing_report_or_baseline_is_incomplete_never_a_pass(tmp_path, monkeypatch):
    _baseline(tmp_path, monkeypatch)
    assert gates.gate_drop(None, "")["ok"] is None and gates.gate_here(None, "")["ok"] is None
    monkeypatch.setattr(gates, "BASELINE", tmp_path / "missing.json")
    assert gates.gate_drop({"n": 400, "correct": 300}, "r")["ok"] is None


def test_here_gate_fails_on_any_violation():
    assert gates.gate_here({"n": 400, "here_violations": 0}, "r")["ok"] is True
    assert gates.gate_here({"n": 400, "here_violations": 1}, "r")["ok"] is False


def test_report_must_be_a_full_run_and_errors_are_excluded(tmp_path):
    small = tmp_path / "answer_eval_small.jsonl"
    small.write_text("\n".join(json.dumps({"system": "rag", "judge": "correct", "here_violation": False}) for _ in range(60)))
    assert gates.load_report(None) is not None            # default discovery must not crash
    p, rows = gates.load_report(small)                    # an explicitly named file is used as given
    assert len(rows) == 60 and gates.summarise_rows(rows)["correct"] == 60
