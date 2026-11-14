"""'with and without traffic' questions: the extractor lists one item per scenario; each must be priced once, not twice."""
from lbs_rag.tools import pricing_intent


def test_with_and_without_traffic_not_double_counted(monkeypatch):
    items = [{"provider": p, "api": "directions", "requests": 1_000_000, "traffic": t, "basic": False}
             for t in (True, False) for p in ("Ola Maps", "Google Maps Platform")]
    monkeypatch.setattr(pricing_intent, "extract", lambda q: {"pricing": True, "items": items})
    q = "Compare Ola Maps and Google for 1,000,000 traffic-aware route requests a month in India. What if we drop live traffic?"
    res = pricing_intent.run(q)
    skus = [r["sku"] for r in res["results"]]
    assert len(skus) == len(set(skus)) == 4
    ola = {r["sku"]: r["cost"] for r in res["results"] if r["provider"] == "Ola Maps"}
    assert ola == {"ola.directions": "179100.00", "ola.directions_basic": "89100.00"}
