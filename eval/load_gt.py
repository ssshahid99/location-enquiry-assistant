"""Load ground truth: items with split + gold source URLs (doc-level gold from gt_citations.csv)."""
from pathlib import Path

import pandas as pd

GT = Path(__file__).resolve().parents[1] / "data" / "ground_truth"


def load_items(split: str | None = None) -> list[dict]:
    gt = pd.read_csv(GT / "gt.csv")
    cites = pd.read_csv(GT / "gt_citations.csv")
    gold = cites.groupby("ID")["doc_url"].apply(lambda s: sorted(set(s))).to_dict()
    items = []
    for r in gt.to_dict("records"):
        urls = set(gold.get(r["ID"], []))
        if isinstance(r.get("source_link"), str):
            urls.add(r["source_link"])
        r["gold_urls"] = sorted(urls)
        items.append(r)
    if split:
        items = [i for i in items if i["split"] == split]
    return items
