"""No-retrieval baseline: Qwen3-8B answers from its own knowledge. Shows what retrieval + calculator add."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from config.settings import openai_client, settings  # noqa: E402

SYSTEM = ("You are a sales and support assistant for location APIs (Google Maps Platform, Mapbox, HERE, Ola Maps, "
          "OSM Nominatim, OSRM) for customers in India. Answer concisely and include specific prices or limits when you know them.")


def baseline_answer(query: str) -> dict:
    r = openai_client().chat.completions.create(
        model=settings.chat_model, temperature=0, max_tokens=700,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": query + "\n/no_think"}])
    text = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
    return {"text": text, "citations": [], "tool_calls": [], "abstained": False, "escalate": False,
            "reason": None, "trace_id": "baseline"}
