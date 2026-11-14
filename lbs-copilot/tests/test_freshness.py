import json
from datetime import datetime, timezone

import pytest

from lbs_rag.ingest import freshness as F
from lbs_rag.ingest.fetch import slug


def page(body, junk=""):
    return f"<html><head><title>T</title></head><body>{junk}<main><h1>Pricing</h1><p>{body}</p></main></body></html>"


@pytest.fixture()
def snaps(tmp_path):
    srcs = [{"url": f"https://example.com/{n}", "kind": "docs", "provider": "X"} for n in ("a", "b", "c", "d")]
    for day, bodies in {"2026-10-01": {"a": "Price is 5 per 1000 requests.", "b": "Limit is 100.", "c": "Same text.", "d": "Old only."},
                        "2026-11-01": {"a": "Price is 7 per 1000 requests.", "b": "Limit is 100.", "c": "Same text."}}.items():
        (tmp_path / day).mkdir()
        for s in srcs:
            n = s["url"][-1]
            if n in bodies:
                junk = "<script>var nonce='%s'</script>" % day if n == "b" else ""      # page noise must not count as a change
                (tmp_path / day / f"{slug(s['url'])}.html").write_text(page(bodies[n], junk), encoding="utf-8")
    srcs.append({"url": "https://example.com/e", "kind": "docs", "provider": "X"})        # present only in the new snapshot
    (tmp_path / "2026-11-01" / f"{slug('https://example.com/e')}.html").write_text(page("New page."), encoding="utf-8")
    return tmp_path, srcs


def test_compare_finds_changed_added_removed_and_ignores_page_noise(snaps):
    root, srcs = snaps
    r = F.compare("2026-10-01", "2026-11-01", raw_root=root, sources=srcs)
    short = lambda k: sorted(u[-1] for u in r[k])  # noqa: E731
    assert short("changed") == ["a"] and short("added") == ["e"] and short("removed") == ["d"]
    assert short("unchanged") == ["b", "c"] and r["unreadable"] == []                       # b differs only by a script nonce


def test_a_snapshot_compared_with_itself_has_no_changes(snaps):
    root, srcs = snaps
    r = F.compare("2026-11-01", "2026-11-01", raw_root=root, sources=srcs)
    assert r["changed"] == r["added"] == r["removed"] == []


def test_unparseable_page_is_reported_not_silently_unchanged(snaps):
    root, srcs = snaps
    s = [x for x in srcs if x["url"].endswith("/c")][0]
    s["kind"] = "stackoverflow"                                                              # this page is now expected as JSON ...
    for day in ("2026-10-01", "2026-11-01"):
        (root / day / f"{slug(s['url'])}.json").write_text("<<not json>>", encoding="utf-8")   # ... but the file is garbage
    r = F.compare("2026-10-01", "2026-11-01", raw_root=root, sources=srcs)
    assert s["url"] in r["unreadable"]


def test_replace_doc_chunks_swaps_only_the_listed_pages_and_keeps_order():
    ch = lambda u, i: {"doc_url": u, "chunk_id": f"{u}{i}", "text": f"{u}{i}"}  # noqa: E731
    old = [ch("a", 0), ch("a", 1), ch("b", 0), ch("c", 0)]
    out = F.replace_doc_chunks(old, {"a": [ch("a", 9)], "d": [ch("d", 0)]}, ["a", "b", "c", "d"])
    assert [c["chunk_id"] for c in out] == ["a9", "b0", "c0", "d0"]                          # a's two old chunks replaced by one; others untouched
    assert F.replace_doc_chunks(old, {}, ["a", "b", "c"]) == old


def test_snapshot_age_and_stale_flag(tmp_path):
    p = tmp_path / "chunks.jsonl"
    p.write_text("\n".join(json.dumps({"fetched_at": d}) for d in ("2026-09-01T10:00:00+00:00", "2026-11-08T10:00:00+00:00")), encoding="utf-8")
    i = F.snapshot_info(p, now=datetime(2026, 11, 9, tzinfo=timezone.utc))
    assert i["oldest"] == "2026-09-01" and i["newest"] == "2026-11-08" and i["age_days"] == 69 and i["stale"] is True
    j = F.snapshot_info(p, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert j["stale"] is False
    p.write_text("", encoding="utf-8")
    assert F.snapshot_info(p)["stale"] is None


def test_real_snapshot_compared_with_itself_is_clean():
    days = F.snapshots()
    if not days:
        pytest.skip("no local snapshot")
    r = F.compare(days[-1], days[-1])
    assert r["changed"] == [] and r["unreadable"] == [] and len(r["unchanged"]) > 100
