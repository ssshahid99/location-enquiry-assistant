"""Build eval/review/agent_review.xlsx: ~30 agent drafts and handoffs for a HUMAN reviewer (checklist A7).

Each row is one customer message run through the real chain (Router -> Planner -> Executor -> Critic, real RAG, gateway needed) in a
throw-away database, so nothing here touches real cases. The reviewer judges three things: was the route right, is the draft acceptable
to send (after the approval step), and is the handoff package usable by the next person.
There is NO AI verdict column filled in: if someone adds one it must stay labelled UNREVIEWED (the "Claude verdict" column is left blank
on purpose). Selection is spread across routes and categories, development split only (the holdout is never used here).
Usage: python eval/make_agent_review_sheet.py [--n 30] [--refresh]      Answers are cached in eval/review/agent_drafts_cache.json
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from lbs_rag.agent import orchestrator, router  # noqa: E402
from lbs_rag.store.db import connect  # noqa: E402

REVIEW = ROOT / "eval" / "review"
CACHE = REVIEW / "agent_drafts_cache.json"
COLS = [("ID", 14), ("Intent (ground truth)", 18), ("Customer message", 48), ("Route and why", 34), ("Planner: category / urgency / questions", 42),
        ("Draft reply (sent only after approval)", 62), ("Critic verdict and issues", 34), ("Handoff package", 62), ("Sources cited", 36),
        ("Claude verdict (UNREVIEWED, left blank)", 18),
        ("REVIEWER: route right?", 14), ("REVIEWER: draft", 16), ("REVIEWER: handoff usable?", 14), ("REVIEWER notes", 40)]


def pick(items: list[dict], n: int) -> list[dict]:
    """Round-robin over (route, category) so every route and the main categories appear; deterministic."""
    buckets: dict[tuple, list] = {}
    for it in items:
        d = router.route(it["Question"])
        if d["route"] == "refused":
            continue
        pl = d.get("plan") or {}
        buckets.setdefault((d["route"], pl.get("category") or "-"), []).append(it)
    order = sorted(buckets, key=lambda k: (k[0] != "agent", k[0] != "human", -len(buckets[k]), k))
    out: list[dict] = []
    while len(out) < n and any(buckets.values()):
        for k in order:
            if buckets[k] and len(out) < n:
                out.append(buckets[k].pop(0))
    return out


def run_one(con, it: dict) -> dict:
    r = orchestrator.handle_message(con, it["Question"])
    h = r.get("handoff") or (orchestrator.prepare_handoff(con, r["case_id"]) if r.get("case_id") else None)
    crit = r.get("critic") or {}
    return {"id": it["ID"], "intent": it["intent"], "message": it["Question"], "route": r["route"], "why": r["reason"],
            "category": r.get("category"), "urgency": r.get("urgency"), "questions": [q["question"] for q in r.get("questions", [])],
            "draft": r.get("draft") or r.get("text") or "(no customer draft: a person decides)",
            "critic": f"{crit.get('verdict', '-')} after {crit.get('revisions', 0)} revision(s); " + "; ".join(
                f"[{i['severity']}] {i['check']}: {i['detail']}" for i in crit.get("issues", [])) if crit else "(not applicable on this route)",
            "handoff": (h["text"] + ("\nGAPS: " + "; ".join(i["detail"] for i in h["issues"]) if h["issues"] else "")) if h else "(none: answered directly)",
            "sources": sorted({c["doc_url"] for c in (r.get("knowledge") or {}).get("citations", r.get("citations", []))}),
            "model_calls": r.get("model_calls")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and rerun every message (uses the gateway)")
    a = ap.parse_args()
    REVIEW.mkdir(parents=True, exist_ok=True)
    cache = {} if a.refresh or not CACHE.exists() else json.loads(CACHE.read_text(encoding="utf-8"))
    picked = pick(load_items("eval"), a.n)
    con = connect(Path(tempfile.mkdtemp()) / "agent_review.db")
    rows = []
    for it in picked:
        if it["ID"] not in cache:
            try:
                cache[it["ID"]] = run_one(con, it)
            except Exception as e:  # noqa: BLE001
                cache[it["ID"]] = {"id": it["ID"], "intent": it["intent"], "message": it["Question"], "route": "error", "why": f"{type(e).__name__}: {e}",
                                   "category": None, "urgency": None, "questions": [], "draft": "", "critic": "", "handoff": "", "sources": []}
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append(cache[it["ID"]])
        print(f"{it['ID']}: {cache[it['ID']]['route']}")

    wb = Workbook()
    ws = wb.active
    ws.title = "Agent drafts"
    ws.append([c for c, _ in COLS])
    for i, (c, w) in enumerate(COLS, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        ws.cell(1, i).font = Font(bold=True)
        ws.cell(1, i).fill = PatternFill("solid", fgColor="FFF2CC" if c.startswith(("REVIEWER", "Claude")) else "DDEBF7")
        ws.cell(1, i).alignment = Alignment(wrap_text=True, vertical="top")
    for r in rows:
        ws.append([r["id"], r["intent"], r["message"], f"{r['route']}: {r['why']}",
                   f"{r.get('category')} / {r.get('urgency')}\n" + "\n".join(f"- {q}" for q in r["questions"]),
                   r["draft"], r["critic"], r["handoff"], "\n".join(r["sources"]), "", "", "", "", ""])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    last = ws.max_row
    for col, opts in (("K", "yes,no,unsure"), ("L", "send as is,send after edits,reject"), ("M", "yes,partly,no")):
        dv = DataValidation(type="list", formula1=f'"{opts}"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{last}")
    ws.freeze_panes = "D2"
    guide = wb.create_sheet("How to review")
    for line in ["Agent draft review (about 30 cases)",
                 "Nothing in these rows was sent to anyone: the draft only becomes available after an approver approves it.",
                 "For each row fill the three yellow REVIEWER columns: route right? / draft (send as is, send after edits, reject) / handoff usable?",
                 "Judge the draft as if you were the person about to send it: wrong facts, invented promises, missing questions and tone all count.",
                 "The Claude column is intentionally empty. Any AI verdict added later must be marked UNREVIEWED.",
                 "Add anything surprising in REVIEWER notes; those notes feed the next round of fixes."]:
        guide.append([line])
    guide.column_dimensions["A"].width = 130
    path = REVIEW / "agent_review.xlsx"
    wb.save(path)
    print(f"wrote {path} ({len(rows)} cases)")


if __name__ == "__main__":
    main()
