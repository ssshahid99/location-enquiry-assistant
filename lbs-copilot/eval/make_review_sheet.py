"""Build eval/review/answer_review.xlsx: ~30 system answers for a human reviewer.

Every row carries Claude's provisional verdict (from verdicts.json, written after reading each answer) clearly marked
UNREVIEWED, plus blank columns for the human reviewer. Selection: spread across intents, favouring items the automatic
judge did not mark correct. Answers are regenerated so the sheet shows current output with its citations.
Usage: python eval/make_review_sheet.py [--n 30] [--from-report <jsonl>]   (then fill eval/review/verdicts.json, rerun)
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from lbs_rag.generate.answer import answer  # noqa: E402

REVIEW = ROOT / "eval" / "review"
COLS = [("ID", 16), ("Intent", 18), ("Question", 45), ("Reference answer (ground truth)", 55), ("System answer", 60),
        ("Cited sources", 40), ("Calculator figures", 30), ("Auto-judge (Qwen, weak)", 14),
        ("Claude verdict (UNREVIEWED)", 16), ("Claude reasoning (UNREVIEWED)", 50),
        ("REVIEWER verdict", 16), ("REVIEWER notes", 40)]


def pick(rows: list[dict], n: int) -> list[str]:
    """Round-robin across intents, non-correct first, so weak spots are over-represented."""
    by: dict[str, list] = {}
    for r in sorted(rows, key=lambda r: (r["judge"] == "correct", r["id"])):
        by.setdefault(r["intent"], []).append(r["id"])
    ids: list[str] = []
    while len(ids) < n and any(by.values()):
        for k in sorted(by, key=lambda k: -len(by[k])):
            if by[k] and len(ids) < n:
                ids.append(by[k].pop(0))
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--from-report", default="")
    ap.add_argument("--tag", default="", help="e.g. v2: separate cache/verdicts/xlsx; selection.json is shared (same 30 questions)")
    a = ap.parse_args()
    sfx = f"_{a.tag}" if a.tag else ""
    REVIEW.mkdir(parents=True, exist_ok=True)
    rep = a.from_report or sorted(glob.glob(str(ROOT / "eval" / "reports" / "answer_eval_*.jsonl")), key=os.path.getmtime)[-1]
    rows = [json.loads(l) for l in open(rep, encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r["system"] == "rag" and "error" not in r]
    sel_path = REVIEW / "selection.json"
    ids = json.loads(sel_path.read_text()) if sel_path.exists() else pick(rows, a.n)
    sel_path.write_text(json.dumps(ids))
    judge = {r["id"]: r["judge"] for r in rows}
    items = {i["ID"]: i for i in load_items("eval")}

    cache_path = REVIEW / f"answers_cache{sfx}.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    for i in ids:
        if i not in cache:
            ans = answer(items[i]["Question"], persist=False)
            cache[i] = {"text": ans["text"], "urls": [c["doc_url"] for c in ans["citations"]],
                        "calc": [f"{r['provider']} / {r['label']}: {r['currency']} {r['cost']}"
                                 + (f" = INR {r['cost_inr']}" if r["currency"] != "INR" else "")
                                 for t in ans["tool_calls"] for r in t.get("output", [])],
                        "unverified": [u for t in ans["tool_calls"] for u in t.get("unverified", [])],
                        "abstained": ans["abstained"]}
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    vpath = REVIEW / f"verdicts{sfx}.json"
    verdicts = json.loads(vpath.read_text(encoding="utf-8")) if vpath.exists() else {}

    wb = Workbook()
    ws0 = wb.active
    ws0.title = "READ ME"
    for line in [
        "Answer review sheet - Location Services Sales and Support Copilot (RAG)",
        "",
        "STATUS: ALL 'Claude verdict' ENTRIES ARE PROVISIONAL AND UNREVIEWED. They are one model's reading, not ground truth.",
        "",
        "Your task: for each row on the 'Answers' sheet, compare 'System answer' with the 'Reference answer' and fill",
        "'REVIEWER verdict' (dropdown) and, where useful, 'REVIEWER notes'. Disagree with Claude freely; disagreements are the most useful data.",
        "",
        "Verdict meanings:",
        "  correct   - key facts and numbers agree with the reference; nothing material is wrong or invented",
        "  partial   - right direction but missing a key fact/number, or includes a small error",
        "  incorrect - wrong numbers, wrong cause, or invents facts not in the reference",
        "  abstained-ok  - system correctly declined (e.g. HERE price not verified) and gave no made-up number",
        "  abstained-bad - system declined but the reference shows the answer was available",
        "",
        "Also check: (1) do the cited sources plausibly support the claims they follow? (2) any price stated that is not in",
        "'Calculator figures'? Note problems in 'REVIEWER notes'.",
        "",
        "Known limits: Ola Distance Matrix free allowance (pairs vs requests) is unconfirmed; HERE prices are intentionally unavailable.",
        "'Auto-judge' is Qwen3-8B grading itself, and is known to be generous.",
    ]:
        ws0.append([line])
    ws0.column_dimensions["A"].width = 130
    ws0["A1"].font = Font(bold=True, size=14)
    ws0["A3"].font = Font(bold=True, color="C00000")

    ws = wb.create_sheet("Answers")
    ws.append([c for c, _ in COLS])
    for j, (c, w) in enumerate(COLS, 1):
        ws.column_dimensions[ws.cell(1, j).column_letter].width = w
        ws.cell(1, j).font = Font(bold=True)
        ws.cell(1, j).fill = PatternFill("solid", fgColor="DDEBF7")
    for i in ids:
        c, v = cache[i], verdicts.get(i, {})
        calc = "\n".join(c["calc"]) + ("\nUNVERIFIED: " + "; ".join(c["unverified"]) if c["unverified"] else "")
        ws.append([i, items[i]["intent"], items[i]["Question"], str(items[i]["Answer"]), c["text"],
                   "\n".join(dict.fromkeys(c["urls"])), calc, judge.get(i, ""),
                   v.get("verdict", "(pending)"), v.get("reason", ""), "", ""])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        row[8].fill = PatternFill("solid", fgColor="FFF2CC")    # Claude (unreviewed) = yellow
        row[10].fill = PatternFill("solid", fgColor="E2EFDA")   # reviewer = green
    dv = DataValidation(type="list", formula1='"correct,partial,incorrect,abstained-ok,abstained-bad"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"K2:K{len(ids) + 1}")
    ws.freeze_panes = "D2"
    out = REVIEW / f"answer_review{sfx}.xlsx"
    wb.save(out)
    print(f"wrote {out} with {len(ids)} rows; Claude verdicts filled: {sum(1 for i in ids if i in verdicts)}")


if __name__ == "__main__":
    main()
