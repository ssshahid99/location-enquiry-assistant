"""Stack Overflow snapshot JSON -> sections: question, then accepted/top answers (CC BY-SA 4.0)."""
from datetime import datetime, timezone

from bs4 import BeautifulSoup

MAX_ANSWERS = 3


def html_to_text(body: str) -> str:
    soup = BeautifulSoup(body, "lxml")
    for pre in soup.find_all("pre"):
        pre.replace_with("\n```\n" + pre.get_text() + "\n```\n")
    for a in soup.find_all("a"):
        a.replace_with(a.get_text())
    return soup.get_text().strip()


def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def parse_stackoverflow(d: dict) -> tuple[str, list[dict], dict]:
    q = d["question"]
    title = BeautifulSoup(q["title"], "lxml").get_text()
    sections = [{"section": "question", "text": f"{title}\n\n{html_to_text(q['body'])}"}]
    answers = sorted(d["answers"], key=lambda a: (not a.get("is_accepted"), -a.get("score", 0)))
    for i, a in enumerate(answers[:MAX_ANSWERS]):
        tag = "accepted answer" if a.get("is_accepted") else f"answer {i + 1}"
        sections.append({"section": f"{tag} (score {a.get('score', 0)})", "text": html_to_text(a["body"])})
    extra = {"effective_date": _date(q.get("last_activity_date") or q["creation_date"]),
             "created": _date(q["creation_date"]), "tags": q.get("tags", [])}
    return title, sections, extra
