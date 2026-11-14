from lbs_rag.retrieve import hybrid
from lbs_rag.retrieve.hybrid import is_cross_provider


def test_cross_provider_questions_are_detected():
    for q in ("We run 16-tonne trucks between Indian warehouses. Which routing APIs support truck routing?",
              "Do all the routing providers support walking and cycling directions in India?",
              "Which matrix APIs give traffic-aware durations, and what's the catch for each?"):
        assert is_cross_provider(q, [])


def test_named_provider_or_plain_question_is_not_cross_provider():
    assert not is_cross_provider("Which routing APIs support trucks?", ["Ola Maps"])
    assert not is_cross_provider("Why does Ola Directions return 422 for Dubai?", [])
    assert not is_cross_provider("How much does 1M directions requests cost?", [])


def test_cover_adds_missing_providers_without_dropping_the_top_k(monkeypatch):
    def fake_hit(p, i, s):
        return {"chunk_id": f"{p}{i}", "provider": p, "score": s}
    base = [fake_hit("Google Maps Platform", i, 1 - i / 10) for i in range(8)]
    monkeypatch.setattr(hybrid, "bm25_search", lambda q, f=None, n=0: [fake_hit(f["provider"], 0, 0.5)] if f else [])
    monkeypatch.setattr(hybrid, "dense_search", lambda q, f=None, n=0: [])
    out = hybrid._cover("q", ["q"], base, k=6)
    assert [h["chunk_id"] for h in out[:6]] == [h["chunk_id"] for h in base[:6]]
    assert len(out) == 6 + 4
    assert {h["provider"] for h in out[6:]} <= {"Ola Maps", "Mapbox", "OSRM", "HERE", "Mappls (MapmyIndia)"}
