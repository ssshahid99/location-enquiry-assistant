"""Load ground truth: items with split + gold source URLs (doc-level gold from the citations file).

Uses the merged v3 files (eval/build_gt_v3.py: ground truth v2.4 + legacy HERE items, with our own sealed-holdout rule) when present,
otherwise the original 500-question files.

  load_items("eval")      development data = tiers core + working + legacy_here_dev     (default for every tuning / debugging tool)
  load_items("working")   development data WITHOUT the frozen core: the only set that may be used to tune or to pick examples
  load_items("core")      the frozen 80-record scoreboard: run it, never tune to individual items
  load_items("holdout")   the sealed holdout: REFUSED unless allow_holdout=True (final run only, once)
  load_items(None)        same as "eval"; "all" also needs allow_holdout=True
"""
from pathlib import Path

import pandas as pd

GT = Path(__file__).resolve().parents[1] / "data" / "ground_truth"
V3 = GT / "gt_v3.csv"


def _tier_filter(items: list[dict], split: str | None) -> list[dict]:
    if split in (None, "eval"):
        return [i for i in items if i["split"] == "eval"]
    if split == "working":
        return [i for i in items if i["split"] == "eval" and i.get("tier") != "core"]
    if split == "core":
        return [i for i in items if i.get("tier") == "core"]
    if split == "holdout":
        return [i for i in items if i["split"] == "holdout"]
    return items                                                    # "all"


def load_items(split: str | None = None, *, allow_holdout: bool = False) -> list[dict]:
    if split in ("holdout", "all") and not allow_holdout:
        raise PermissionError("the sealed holdout may only be loaded for the final run (allow_holdout=True); see data-usage rules")
    v3 = V3.exists()
    gt = pd.read_csv(V3 if v3 else GT / "gt.csv", keep_default_na=False)
    cites = pd.read_csv(GT / ("gt_v3_citations.csv" if v3 else "gt_citations.csv"))
    gold = cites.groupby("ID")["doc_url"].apply(lambda s: sorted(set(s))).to_dict()
    items = []
    for r in gt.to_dict("records"):
        urls = set(gold.get(r["ID"], []))
        if isinstance(r.get("source_link"), str) and r["source_link"]:
            urls.add(r["source_link"])
        r["gold_urls"] = sorted(urls)
        r.setdefault("tier", "working" if r["split"] == "eval" else "holdout")
        items.append(r)
    return _tier_filter(items, split)
