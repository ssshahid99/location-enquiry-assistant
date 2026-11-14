"""Build the merged ground truth `gt_v3` from ground-truth v2.4 (refined, 521 records) + the legacy HERE items of our previous file.

Decisions (Deepshikha, 2026-11-14):
  * HERE stays in scope: every old item that involves HERE is kept (v2.4 removed or rewrote the HERE content), as a "legacy_here" row.
  * The sealed HOLDOUT = questions we had never used: the old holdout (never touched) + the new questions added in v2.4, MINUS the frozen
    core (a scoreboard that is run repeatedly) and MINUS any question whose template family also has a question we HAVE seen (leakage).
    Everything we have seen (the old development set) is development data, whatever v2.4's own split column says.
  * The frozen core (80, verified_live) keeps its v2.4 membership: tier "core" (never tuned to individual items, never used as examples).
Output (data/ground_truth/): gt_v3.csv, gt_v3_citations.csv, gt_v3_report.json.  Usage: python eval/build_gt_v3.py
Tiers: core | working | holdout | legacy_here_dev | legacy_here_holdout.  `split` stays eval|holdout so older tools keep working.
"""
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GT = ROOT / "data" / "ground_truth"
V24 = GT / "ground-truth-v2.4.xlsx"
SRC_DEFAULT = Path(r"C:\Users\Deepshikha\Downloads\ground-truth (1).xlsx")

API_OLD = {"directions": "Directions", "distance_matrix": "Distance Matrix", "autocomplete": "Autocomplete", "reverse_geocode": "Reverse Geocode"}


def parse_escalation(s) -> dict:
    s = str(s or "")
    g = lambda k: (re.search(rf"{k}=([^;]+)", s) or [None, None])[1]  # noqa: E731
    return {"should_escalate": str(g("should_escalate")).strip().lower() == "true", "severity": (g("severity") or "").strip(),
            "owner": (g("owner") or "").strip(), "escalation_reason": (g("reason") or "").strip()}


def main(src: Path = SRC_DEFAULT) -> dict:
    if not V24.exists():
        shutil.copy2(src, V24)
    d = pd.read_excel(V24, sheet_name="Data")
    cit = pd.read_excel(V24, sheet_name="citations")
    old = pd.read_csv(GT / "gt.csv")
    old_cit = pd.read_csv(GT / "gt_citations.csv")

    old_dev, old_hold = set(old[old.split == "eval"].ID), set(old[old.split == "holdout"].ID)
    new_ids = set(d.id) - set(old.ID)
    core = set(d[d.frozen_core == True].id)  # noqa: E712
    seen = set(d.id) - ((old_hold & set(d.id)) | new_ids)            # items we have looked at (old development set)
    fam = d.set_index("id").template_family
    seen_fams = set(fam.loc[list(seen)].dropna())
    unseen = (old_hold & set(d.id)) | new_ids
    leaked = {i for i in unseen - core if fam.get(i) in seen_fams}
    sealed = unseen - core - leaked

    rows = []
    for r in d.to_dict("records"):
        i = r["id"]
        tier = "core" if i in core else "holdout" if i in sealed else "working"
        e = parse_escalation(r["escalation"])
        rows.append({"ID": i, "Question": r["question"], "Answer": r["reference_answer"], "API": API_OLD.get(r["api"], r["api"]),
                     "Support/Sales": str(r["primary_team"]).lower(), "Service Providers": r["service_providers"],
                     "split": "holdout" if tier == "holdout" else "eval", "tier": tier, "difficulty": r["difficulty"],
                     "intent": r["sub_intent"], "mapped_intent": r["intent"], "bucket": r["bucket"], "should_escalate": e["should_escalate"],
                     "severity": e["severity"], "owner": e["owner"], "escalation_reason": e["escalation_reason"],
                     "source_link": "", "flag": r["flag_reason"], "verification_status": r["verification_status"], "frozen_core": i in core,
                     "template_family": r["template_family"], "expected_tools": r["expected_tools"], "gold_tool_trace": r["gold_tool_trace"],
                     "gold_clarifying_questions": r["gold_clarifying_questions"], "rubric": r["rubric"], "context": r["context"],
                     "origin": "v2.4", "was_seen": i in seen})
    # legacy HERE items: the previous file's versions, kept as separate rows (split by their OLD split, so seen/unseen stays truthful)
    here = old[old["Service Providers"].astype(str).str.contains("HERE")]
    for r in here.to_dict("records"):
        tier = "legacy_here_holdout" if r["split"] == "holdout" else "legacy_here_dev"
        rows.append({"ID": f"LEGACY:{r['ID']}", "Question": r["Question"], "Answer": r["Answer"], "API": r["API"], "Support/Sales": r["Support/Sales"],
                     "Service Providers": r["Service Providers"], "split": r["split"], "tier": tier, "difficulty": r["difficulty"], "intent": r["intent"],
                     "mapped_intent": "", "bucket": "", "should_escalate": bool(r["should_escalate"]), "severity": "", "owner": "", "escalation_reason": "",
                     "source_link": r["source_link"], "flag": r["flag"], "verification_status": "legacy_unverified", "frozen_core": False,
                     "template_family": "", "expected_tools": "", "gold_tool_trace": "", "gold_clarifying_questions": "", "rubric": "", "context": "",
                     "origin": "legacy_here", "was_seen": r["split"] != "holdout"})
    out = pd.DataFrame(rows)
    out.to_csv(GT / "gt_v3.csv", index=False, encoding="utf-8")

    c1 = cit.rename(columns={"id": "ID"})[["ID", "doc_url", "span", "supports_claim", "quote_or_summary"]]
    c2 = old_cit[old_cit.ID.isin(here.ID)].copy()
    c2["ID"] = "LEGACY:" + c2["ID"]
    pd.concat([c1, c2[c1.columns]]).to_csv(GT / "gt_v3_citations.csv", index=False, encoding="utf-8")

    rep = {"total": len(out), "by_tier": out.tier.value_counts().to_dict(),
           "sealed_holdout_v24": len(sealed), "family_leakers_moved_to_working": len(leaked), "unseen_total": len(unseen),
           "unseen_in_core": len(unseen & core), "legacy_here": len(here), "legacy_here_holdout": int((here.split == "holdout").sum()),
           "holdout_total": int((out.split == "holdout").sum()), "working_total": int(((out.split == "eval") & (out.tier != "core")).sum()),
           "holdout_by_api": out[out.split == "holdout"].API.value_counts().to_dict(),
           "holdout_by_mapped_intent": out[(out.split == "holdout") & (out.origin == "v2.4")].mapped_intent.value_counts().to_dict(),
           "holdout_by_verification": out[out.split == "holdout"].verification_status.value_counts().to_dict(),
           "seen_by_assistant_in_analysis": ["GT-LBS-DIR-201", "GT-LBS-DIR-202", "GT-LBS-DIR-203", "GT-LBS-DIR-204"]}
    (GT / "gt_v3_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return rep


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT), indent=1))
