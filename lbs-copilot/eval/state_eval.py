"""Activity 13 evidence: on the history-bucket items, does the chat ask a question that the conversation already answered or already asked?

Each history item is a 3-turn conversation: [user] question, [assistant] one clarifying question, [user] reply ("not sure, go ahead" or the detail).
The state is seeded the way the chat would have stored it (turn 1 facts, the assistant's question as asked, the original question pending), then
the final customer turn goes through the real chat. A response VIOLATES if any question in it (a sentence ending in '?', or a clarifying question)
(a) repeats an earlier assistant question (content-word overlap >= 0.5), or (b) asks for something the customer already told us (volume, provider/API,
region, timeline, budget). Pass (sheet): >= 95% of responses violate neither, and state survives a restart (tested in tests/test_chat.py).
Usage: python eval/state_eval.py [--tier working|core|eval]    (uses the chat gateway; core is a scoreboard, never tuned on)
"""
import argparse
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

from lbs_rag.agent import planner as case_rules  # noqa: E402
from lbs_rag.chat import state  # noqa: E402
from lbs_rag.chat.chat import chat  # noqa: E402
from lbs_rag.store.db import connect  # noqa: E402

_STOP = set("the a an of to for is are do does you your we our in on and or what which how any can will would should be it that this with at by from as if".split())
ASKS = {"volume": re.compile(r"how many (?:requests|calls|lookups|elements)|monthly volume|expected volume|requests per month", re.I),
        "provider": re.compile(r"which (?:provider|api|vendor)|what (?:provider|api)", re.I),
        "regions": re.compile(r"which (?:cities|regions|countries)|where (?:are|will) you (?:operate|use)", re.I),
        "timeline": re.compile(r"go-?live|decision date|timeline|when do you (?:need|plan)", re.I),
        "budget": re.compile(r"\bbudget\b|price target", re.I)}
FACT_OF = {"volume": "volume", "provider": "provider", "regions": "regions", "timeline": "timeline", "budget": "budget"}


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP and len(w) > 2}


def questions_in(text: str, clar: list[dict]) -> list[str]:
    qs = [s.strip() for s in re.split(r"(?<=[?.!])\s+|\n", text) if s.strip().endswith("?")]
    return qs + [q["text"] for q in clar]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="working")
    a = ap.parse_args()
    items = [i for i in load_items(a.tier) if str(i.get("bucket")) == "history"]
    con = connect(Path(tempfile.mkdtemp()) / "state_eval.db")
    bad, asked_any = [], 0
    for k, it in enumerate(items, 1):
        parts = re.split(r"\[(user|assistant)\]\s*", it["Question"])
        turns = [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]
        users = [t for r, t in turns if r == "user"]
        assist = [t for r, t in turns if r == "assistant"]
        if len(users) < 2 or not assist:
            continue
        cid = f"H-{it['ID']}"
        state.create(con, "eval", cid)
        state.add_message(con, cid, "customer", users[0], "rag")
        known = case_rules.extract_facts(users[0])
        state.set_facts(con, cid, known)
        state.add_message(con, cid, "assistant", assist[0], "rag")
        state.mark_asked(con, cid, "gold_question", assist[0])
        state.set_pending(con, cid, {"question": users[0]})
        r = chat(con, users[-1], cid, customer="eval", persist=False)
        known = {**known, **case_rules.extract_facts(users[-1])}
        qs = questions_in(r["text"], r["outputs"]["clarifying_questions"]["questions"])
        asked_any += bool(qs)
        why = []
        for q in qs:
            if any(len(words(q) & words(p)) / max(1, len(words(q) | words(p))) >= 0.5 for p in assist):
                why.append(f"repeats an earlier question: {q[:90]}")
            for key, rx in ASKS.items():
                if rx.search(q) and FACT_OF[key] in known:
                    why.append(f"asks for {key} which was already given: {q[:90]}")
        if why:
            bad.append((it["ID"], why))
        if k % 10 == 0:
            print(f"  {k}/{len(items)}", flush=True)
    n = len(items)
    print(f"\n== do not re-ask ({a.tier}, history bucket, n={n}) ==")
    print(f"responses with no repeated or already-answered question  {n - len(bad)}/{n} = {(n - len(bad)) / n:.1%}   (target >= 95%)")
    print(f"responses that asked any question at all                  {asked_any}/{n}")
    for iid, why in bad[:8]:
        print(f"  {iid}: {why[0]}")


if __name__ == "__main__":
    main()
