"""Batch runner that records raw model replies and can replay them.

Purpose: write ``predictions.jsonl`` and ``raw_responses.jsonl`` for one run.
Use case: score a file of inputs, or rebuild the same predictions from the recording.
Example: ``python -m copilot.interpret.run_batch --inputs in.jsonl --out runs/demo``.

Replay does not call the network. Live mode builds ``LLMClient``, which fails
closed without ``TRACKB_API_KEY`` and ``TRACKB_CA_CERT``.

Few-shot examples are loaded before any client is constructed or called.
A missing ``TRACKB_GT_CSV`` aborts the run. ``TRACKB_FEWSHOT=off`` is the
only zero-shot opt-out. ``run_meta.json`` records ``fewshot_mode`` and
``fewshot_loaded``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from copilot.interpret.fewshot import fewshot_status
from copilot.interpret.interpret import interpret
from copilot.llm.client import (
    DEFAULT_MODEL,
    BudgetExceeded,
    Completion,
    ConfigError,
    LLMClient,
    LLMError,
)


def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"


class RecordingClient:
    """Delegates to ``inner`` and stores each call for ``raw_responses.jsonl``."""

    def __init__(self, inner):
        self.inner = inner
        self.events: list[dict] = []

    def complete(self, messages, *, max_tokens: int, temperature: float) -> Completion:
        try:
            response = self.inner.complete(
                messages, max_tokens=max_tokens, temperature=temperature
            )
        except BudgetExceeded:
            self.events.append({"type": "budget_exceeded"})
            raise
        except (ConfigError, LLMError) as exc:
            self.events.append(
                {"type": "model_unavailable", "status_code": getattr(exc, "status_code", None)}
            )
            raise
        self.events.append(
            {
                "type": "ok",
                "text": response.text,
                "raw": response.raw,
                "model": response.model,
                "usage": response.usage,
                "latency_ms": response.latency_ms,
                "think_seen": response.think_seen,
            }
        )
        return response


class ReplayClient:
    """Plays back recorded events in order. Does not open a socket."""

    def __init__(self, events: list[dict]):
        self.events = list(events)

    def complete(self, messages, *, max_tokens: int, temperature: float) -> Completion:
        if not self.events:
            raise LLMError("replay exhausted")
        event = self.events.pop(0)
        kind = event.get("type")
        if kind == "budget_exceeded":
            raise BudgetExceeded("replay budget_exceeded")
        if kind == "model_unavailable":
            raise LLMError("replay unavailable", status_code=event.get("status_code"))
        text = event.get("text") or ""
        return Completion(
            text=text,
            raw=event.get("raw") or text,
            model=event.get("model") or DEFAULT_MODEL,
            usage=event.get("usage"),
            latency_ms=int(event.get("latency_ms") or 0),
            think_seen=bool(event.get("think_seen")),
        )


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def run_batch(
    records: list[dict],
    *,
    out_dir: Path,
    client=None,
    client_factory: Callable[[dict], Any] | None = None,
) -> Path:
    """Write predictions and raw events. Returns the predictions path.

    Loads few-shot examples before the client is used, so a missing GT file
    aborts with no model call. ``run_meta.json`` records the few-shot mode.
    """
    status = fewshot_status()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_meta.json").write_text(
        json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    predictions = out_dir / "predictions.jsonl"
    raw_path = out_dir / "raw_responses.jsonl"
    with predictions.open("w", encoding="utf-8") as pred_file, raw_path.open(
        "w", encoding="utf-8"
    ) as raw_file:
        for record in records:
            inner = client_factory(record) if client_factory else client
            if inner is None:
                raise RuntimeError("run_batch requires a client or client_factory")
            recorder = RecordingClient(inner)
            output = interpret(record, client=recorder)
            payload = output.model_dump(mode="json")
            payload["record_id"] = record.get("record_id")
            pred_file.write(_line(payload))
            raw_file.write(
                _line({"record_id": record.get("record_id"), "events": recorder.events})
            )
    return predictions


def replay_batch(records: list[dict], raw_path: Path, out_dir: Path) -> Path:
    """Rebuild predictions from a raw-response file. No network."""
    events_by_id = {
        row.get("record_id"): row.get("events") or []
        for row in load_jsonl(raw_path)
    }

    def factory(record: dict):
        return ReplayClient(events_by_id.get(record.get("record_id")) or [])

    return run_batch(records, out_dir=out_dir, client_factory=factory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m copilot.interpret.run_batch")
    parser.add_argument("--inputs", required=True, help="JSONL of interpretation inputs")
    parser.add_argument("--out", required=True, help="directory for predictions and raw events")
    parser.add_argument(
        "--replay",
        default=None,
        help="raw_responses.jsonl to replay instead of calling the endpoint",
    )
    args = parser.parse_args(argv)
    records = load_jsonl(Path(args.inputs))
    out_dir = Path(args.out)
    # Fail before LLMClient() so a missing GT file never touches the endpoint.
    fewshot_status()
    if args.replay:
        replay_batch(records, Path(args.replay), out_dir)
    else:
        run_batch(records, out_dir=out_dir, client=LLMClient())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
