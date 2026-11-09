"""Answer eval on the eval split (holdout untouched): correctness, citation quality, abstention, vs a no-RAG baseline.

Metrics
  number_recall   share of ground-truth currency figures that appear in the answer (deterministic)
  judge           Qwen3-8B rubric vs reference: correct / partial / incorrect (weak judge; treat as indicative)
  cite_gold_hit   cited docs include a gold source URL
  cite_support    share of (sentence, [n]) pairs whose cited chunk actually contains the claim's numbers/terms
                  and matches the provider named in the sentence (calculator-derived numbers are excluded)
  uncited         non-abstained answers with no citation at all
  here_violation  answer states a HERE price when ground truth says the HERE price is unverified (must be 0)
Usage: python eval/answer_eval.py --n 60 [--system rag|baseline|both] [--workers 4]
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from baseline_llm import baseline_answer  # noqa: E402
from load_gt import load_items  # noqa: E402

from config.settings import openai_client, settings  # noqa: E402
from lbs_rag.generate.answer import answer  # noqa: E402

CHUNKS = {c["chunk_id"]: c for c in (json.loads(l) for l in
          (ROOT / "data" / "processed" / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
AMOUNT = re.compile(r"(?:₹|INR\s?|USD\s?|\$)\s?([\d,]+(?:\.\d+)?)")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
MARK = re.compile(r"\[(\d+)\]")
STOP = set("which their there these those would could should about other using where while after before because "
           "billed billing price prices cost costs monthly request requests".split())
PROVIDER_WORDS = {"ola": "Ola Maps", "google": "Google Maps Platform", "mapbox": "Mapbox", "here": "HERE",
                  "nominatim": "OSM Nominatim", "osrm": "OSRM"}


def _f(s: str) -> float | None:
    try:
        return float(s.replace(",", "").rstrip("."))
    except ValueError:
        return None


def number_recall(gt: str, text: str) -> float | None:
    # headline totals only (>= 1000): per-unit rates and free-tier sizes repeated in the reference are not the answer
    figs = {x for x in (_f(m) for m in AMOUNT.findall(str(gt))) if x is not None and x >= 1000}
    if not figs:
        return None
    have = {_f(m) for m in NUM.findall(text)} - {None}
    return sum(any(abs(f - h) < 0.011 for h in have) for f in figs) / len(figs)


def judge(question: str, gt: str, cand: str) -> str:
    p = (f"Question: {question}\n\nReference answer:\n{gt}\n\nCandidate answer:\n{cand}\n\n"
         "Does the candidate agree with the reference on the key facts and numbers? Ignore style and length. "
         'Reply ONLY JSON: {"verdict": "correct" | "partial" | "incorrect"}\n/no_think')
    try:
        r = openai_client().chat.completions.create(model=settings.chat_model, temperature=0, max_tokens=60,
                                                    messages=[{"role": "user", "content": p}])
        t = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S)
        return json.loads(re.search(r"\{.*\}", t, flags=re.S).group(0))["verdict"]
    except Exception:  # noqa: BLE001
        return "error"


def calc_numbers(a: dict) -> set[float]:
    out = set()
    for t in a.get("tool_calls", []):
        for r in t.get("output", []):
            for k in ("cost", "cost_inr", "units", "free_units", "billable_units", "contact_sales_units"):
                v = _f(str(r.get(k, "")))
                if v is not None:
                    out.add(v)
    return out


def citation_support(a: dict) -> tuple[int, int]:
    """-> (supported pairs, total pairs)."""
    cites = {c["n"]: CHUNKS.get(c["chunk_id"]) for c in a["citations"]}
    calc = calc_numbers(a)
    ok = tot = 0
    for sent in re.split(r"(?<=[.!?])\s+|\n", a["text"]):
        marks = [int(m) for m in MARK.findall(sent)]
        if not marks or "List-price sources" in sent:     # source attribution lines, not claims
            continue
        claim = MARK.sub("", sent)
        nums = {_f(m) for m in NUM.findall(claim)} - {None}
        nums = {n for n in nums if not any(abs(n - c) < 0.011 for c in calc) and n > 31}   # skip tiny/ordinal numbers
        words = {w for w in re.findall(r"[a-z]{5,}", claim.lower()) if w not in STOP}
        named = {v for k, v in PROVIDER_WORDS.items() if re.search(rf"\b{k}\b", claim.lower())}
        for n in marks:
            ch = cites.get(n)
            if ch is None:
                continue
            tot += 1
            text = ch["text"]
            have = {_f(m) for m in NUM.findall(text)} - {None}
            if nums:
                content_ok = all(any(abs(x - h) < 0.011 for h in have) for x in nums)
            else:
                lw = set(re.findall(r"[a-z]{5,}", text.lower()))
                content_ok = (len(words & lw) / len(words) >= 0.3) if words else True
            prov_ok = not named or ch["provider"] == "Stack Overflow" or ch["provider"] in named
            ok += content_ok and prov_ok
    return ok, tot


def here_violation(item: dict, text: str) -> bool:
    if "HERE list price not verified" not in str(item.get("flag", "")):
        return False
    return bool(re.search(r"\bHERE\b[^.\n]{0,120}(?:₹|INR|USD|\$)\s?\d", text))


RETRIEVAL_OPTS: dict = {}     # set from CLI (--rewrite / --rerank) so A/B runs change one thing


def run_one(item: dict, system: str) -> dict:
    t = time.time()
    try:
        a = answer(item["Question"], persist=False, **RETRIEVAL_OPTS) if system == "rag" else baseline_answer(item["Question"])
    except Exception as e:  # noqa: BLE001
        return {"id": item["ID"], "system": system, "error": f"{type(e).__name__}: {e}"}
    sup, tot = citation_support(a)
    gold = set(item["gold_urls"])
    cited_urls = {c["doc_url"] for c in a["citations"]}
    return {"id": item["ID"], "system": system, "intent": item["intent"], "api": item["API"],
            "question": item["Question"], "gt": item["Answer"], "answer": a["text"], "abstained": a["abstained"],
            "citation_urls": [c["doc_url"] for c in a["citations"]],
            "calc": [(r["label"], r["currency"], r["cost"], r["cost_inr"]) for t in a["tool_calls"] for r in t.get("output", [])],
            "n_recall": number_recall(item["Answer"], a["text"]),
            "judge": judge(item["Question"], str(item["Answer"]), a["text"]),
            "cite_n": len(a["citations"]), "cite_gold_hit": bool(cited_urls & gold) if a["citations"] else None,
            "cite_sup": sup, "cite_tot": tot, "uncited": (not a["citations"]) and not a["abstained"],
            "here_violation": here_violation(item, a["text"]),
            "expect_abstain_here": "HERE list price not verified" in str(item.get("flag", "")),
            "sec": round(time.time() - t, 1)}


def summarize(rows: list[dict], system: str) -> None:
    r = [x for x in rows if x["system"] == system and "error" not in x]
    errs = sum(1 for x in rows if x["system"] == system and "error" in x)
    if not r:
        print(f"{system}: no results ({errs} errors)")
        return
    nr = [x["n_recall"] for x in r if x["n_recall"] is not None]
    j = [x["judge"] for x in r]
    cited = [x for x in r if x["cite_n"]]
    sup, tot = sum(x["cite_sup"] for x in r), sum(x["cite_tot"] for x in r)
    gh = [x["cite_gold_hit"] for x in cited if x["cite_gold_hit"] is not None]
    print(f"\n== {system}  (n={len(r)}, errors={errs}) ==")
    print(f"number_recall        {sum(nr) / len(nr):.3f}   (on {len(nr)} items with currency figures)" if nr else "number_recall n/a")
    print(f"judge correct/partial/incorrect  {j.count('correct')}/{j.count('partial')}/{j.count('incorrect')}  (err {j.count('error')})")
    if system == "rag":
        print(f"cite_support         {sup / tot:.3f}   ({sup}/{tot} sentence-citation pairs)" if tot else "cite_support n/a")
        print(f"cite_gold_hit        {sum(gh) / len(gh):.3f}   (of {len(gh)} answers with citations)" if gh else "cite_gold_hit n/a")
        print(f"uncited answers      {sum(x['uncited'] for x in r)}   abstained {sum(x['abstained'] for x in r)}")
    print(f"HERE price violations {sum(x['here_violation'] for x in r)} of {sum(x['expect_abstain_here'] for x in r)} HERE-unverified items")
    by_intent: dict[str, list] = {}
    for x in r:
        by_intent.setdefault(x["intent"], []).append(x["judge"] == "correct")
    top = sorted(by_intent.items(), key=lambda kv: -len(kv[1]))[:6]
    print("judge-correct by intent: " + ", ".join(f"{k} {sum(v)}/{len(v)}" for k, v in top))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--system", default="both", choices=["rag", "baseline", "both"])
    ap.add_argument("--workers", type=int, default=1, help="gateway allows 1 concurrent request per key")
    ap.add_argument("--no-rewrite", action="store_true", help="rewrite is the default (see answer())")
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--k", type=int, default=6, help="chunks sent to the model")
    ap.add_argument("--pricing-only", action="store_true", help="only pricing_* intents (mostly code-answered, fast)")
    a = ap.parse_args()
    RETRIEVAL_OPTS.update(rewrite=not a.no_rewrite, rerank=a.rerank, k=a.k)
    items = load_items("eval")
    if a.pricing_only:
        items = [i for i in items if str(i["intent"]).startswith("pricing")]
    step = max(1, len(items) // a.n)                  # deterministic spread across intents
    items = items[::step][:a.n]
    systems = ["rag", "baseline"] if a.system == "both" else [a.system]
    rows = []
    from lbs_rag.retrieve.hybrid import retrieve
    retrieve("warm up", k=1)                           # init Chroma + embedder before any threads
    with ThreadPoolExecutor(a.workers) as ex:
        for s in systems:
            rows += list(ex.map(lambda it: run_one(it, s), items))
    out = ROOT / "eval" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"answer_eval_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False, default=str) for x in rows), encoding="utf-8")
    for s in systems:
        summarize(rows, s)
    print(f"\nper-item results: {path}")


if __name__ == "__main__":
    main()
