"""Fetch every source in config/sources.yaml into data/raw/<date>/ (idempotent, dated snapshots).

- httpx for static pages / YAML
- playwright (chromium) for JS-rendered pages
- Stack Exchange API (no key; ~300 req/day limit) for Stack Overflow threads, batched
Writes data/raw/<date>/<slug>.<ext> plus manifest.jsonl (url, status, bytes, fetched_at, method, error).
"""
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[3]
UA = "Mozilla/5.0 (compatible; lbs-copilot-research/0.1)"
SE_API = "https://api.stackexchange.com/2.3"


def slug(url: str) -> str:
    s = re.sub(r"https?://", "", url)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:90]
    return f"{s}_{hashlib.md5(url.encode()).hexdigest()[:6]}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_httpx(client: httpx.Client, url: str) -> tuple[str, str]:
    r = client.get(url, follow_redirects=True, timeout=45)
    r.raise_for_status()
    ext = "yaml" if url.endswith((".yaml", ".yml")) else "html"
    return r.text, ext


def fetch_playwright(pw_page, url: str) -> tuple[str, str]:
    try:
        pw_page.goto(url, wait_until="networkidle", timeout=45000)
    except Exception:  # noqa: BLE001 - chatty pages never go idle; fall back
        pw_page.goto(url, wait_until="domcontentloaded", timeout=60000)
        pw_page.wait_for_timeout(5000)
    pw_page.wait_for_timeout(1500)
    return pw_page.content(), "html"


def fetch_stackoverflow(client: httpx.Client, urls: list[str]) -> dict[str, dict]:
    """Return {url: {question, answers}} using batched SE API calls."""
    ids = {u: u.rstrip("/").split("/")[-1] for u in urls}
    out: dict[str, dict] = {}
    items = list(ids.items())
    for i in range(0, len(items), 50):
        batch = items[i:i + 50]
        idstr = ";".join(v for _, v in batch)
        q = client.get(f"{SE_API}/questions/{idstr}", params={
            "site": "stackoverflow", "filter": "withbody", "pagesize": 100}, timeout=45).json()
        a = client.get(f"{SE_API}/questions/{idstr}/answers", params={
            "site": "stackoverflow", "filter": "withbody", "sort": "votes", "pagesize": 100}, timeout=45).json()
        if "error_id" in q or "error_id" in a:
            raise RuntimeError(f"SE API error: {q.get('error_message') or a.get('error_message')}")
        answers: dict[int, list] = {}
        for ans in a.get("items", []):
            answers.setdefault(ans["question_id"], []).append(ans)
        by_id = {str(x["question_id"]): x for x in q.get("items", [])}
        for url, qid in batch:
            if qid in by_id:
                out[url] = {"question": by_id[qid], "answers": answers.get(int(qid), [])}
        time.sleep(1)
    return out


def main() -> None:
    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    day = datetime.now().strftime("%Y-%m-%d")
    outdir = ROOT / "data" / "raw" / day
    outdir.mkdir(parents=True, exist_ok=True)
    manifest_path = outdir / "manifest.jsonl"
    done = set()
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8-sig").splitlines():
            m = json.loads(line)
            if m["status"] == "ok":
                done.add(m["url"])

    def log(rec: dict) -> None:
        with manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"[{rec['status']:>5}] {rec['method']:<10} {rec['url']} {rec.get('error','')[:80]}")

    todo = [s for s in sources if s["url"] not in done]
    html_src = [s for s in todo if s["fetch_method"] == "httpx"]
    pw_src = [s for s in todo if s["fetch_method"] == "playwright"]
    so_src = [s for s in todo if s["fetch_method"] == "stackexchange_api"]

    with httpx.Client(headers={"User-Agent": UA}) as client:
        for s in html_src:
            try:
                text, ext = fetch_httpx(client, s["url"])
                (outdir / f"{slug(s['url'])}.{ext}").write_text(text, encoding="utf-8")
                log({"url": s["url"], "status": "ok", "method": "httpx", "bytes": len(text), "fetched_at": now()})
            except Exception as e:  # noqa: BLE001 - record and continue
                log({"url": s["url"], "status": "error", "method": "httpx", "error": str(e), "fetched_at": now()})

        if so_src:
            try:
                data = fetch_stackoverflow(client, [s["url"] for s in so_src])
                for s in so_src:
                    d = data.get(s["url"])
                    if d:
                        (outdir / f"{slug(s['url'])}.json").write_text(json.dumps(d), encoding="utf-8")
                        log({"url": s["url"], "status": "ok", "method": "se_api", "bytes": len(json.dumps(d)), "fetched_at": now()})
                    else:
                        log({"url": s["url"], "status": "error", "method": "se_api", "error": "not found/deleted", "fetched_at": now()})
            except Exception as e:  # noqa: BLE001
                for s in so_src:
                    log({"url": s["url"], "status": "error", "method": "se_api", "error": str(e), "fetched_at": now()})

    if pw_src:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=UA)
            for s in pw_src:
                try:
                    text, ext = fetch_playwright(page, s["url"])
                    (outdir / f"{slug(s['url'])}.{ext}").write_text(text, encoding="utf-8")
                    log({"url": s["url"], "status": "ok", "method": "playwright", "bytes": len(text), "fetched_at": now()})
                except Exception as e:  # noqa: BLE001
                    log({"url": s["url"], "status": "error", "method": "playwright", "error": str(e), "fetched_at": now()})
            browser.close()

    recs = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8-sig").splitlines()]
    last = {}
    for r in recs:
        last[r["url"]] = r
    ok = sum(1 for r in last.values() if r["status"] == "ok")
    print(f"\n{ok}/{len(sources)} sources fetched into {outdir}")
    if ok < len(sources):
        sys.exit(1)


if __name__ == "__main__":
    main()
