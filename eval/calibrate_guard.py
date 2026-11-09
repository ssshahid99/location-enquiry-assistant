"""Calibrate the off-topic gate (guard.DOMAIN_SIM_MIN).

For every development-split question WITHOUT a strong domain term we compute the best dense similarity to our corpus; for a set of
clearly off-topic probes we do the same. The threshold must sit below the real questions (no legitimate question refused) and
as far above the off-topic probes as possible. Uses the eval split only; the holdout stays untouched.
Usage: python eval/calibrate_guard.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "eval")]

from load_gt import load_items  # noqa: E402

from lbs_rag.generate import guard  # noqa: E402
from lbs_rag.retrieve.hybrid import dense_search  # noqa: E402

PROBES = [
    "What is the capital of France?", "Give me a recipe for pasta carbonara.", "Tell me a joke.", "Who won the cricket world cup in 2011?",
    "Write a poem about the monsoon.", "Explain quantum physics in simple words.", "What is the weather like in Mumbai today?",
    "How do I lose weight fast?", "Write a Python function to sort a list.", "What are the symptoms of the flu?",
    "Who is the prime minister of India?", "Translate 'good morning' into Spanish.", "What should I name my dog?",
    "Recommend a good movie for tonight.", "How many calories are in a banana?", "What is the meaning of life?",
    "Summarise the plot of Hamlet.", "How do I fix my washing machine?", "What stocks should I buy this year?",
    "Help me write a cover letter for a marketing job.",
]


def sim(q: str) -> float:
    top = dense_search(q, n=1)
    return top[0]["score"] if top else 0.0


def main() -> None:
    items = [i for i in load_items("eval") if not guard.has_strong_domain_term(i["Question"])]
    real = sorted((sim(i["Question"]), i["ID"], i["Question"][:90]) for i in items)
    off = sorted((sim(q), q) for q in PROBES)
    print(f"development questions without a strong domain term: {len(real)}")
    print("lowest 8 real:", [(round(s, 3), i) for s, i, _ in real[:8]])
    print(f"real similarity  min={real[0][0]:.3f}  p5={real[int(len(real) * .05)][0]:.3f}  median={real[len(real) // 2][0]:.3f}")
    print(f"off-topic probes min={off[0][0]:.3f}  median={off[len(off) // 2][0]:.3f}  max={off[-1][0]:.3f}")
    thr = round(real[0][0] - 0.02, 3)
    caught = sum(1 for s, _ in off if s < thr)
    print(f"suggested threshold {thr} (min real - 0.02): catches {caught}/{len(off)} probes, refuses 0/{len(real)} real questions")
    print("probes NOT caught:", [(round(s, 3), q) for s, q in off if s >= thr])


if __name__ == "__main__":
    main()
