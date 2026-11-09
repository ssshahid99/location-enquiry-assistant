"""Grounding check: after the model answers, verify every cited sentence against the passages it cites.

A cited sentence is KEPT only if its numbers appear in the cited passages (or come from the calculator / verified limits / verified
facts), it does not name a provider that none of the cited passages belong to, and it shares enough content words with them.
Unsupported sentences are removed (and the reader is told). If nothing the model cited survives, the whole answer is replaced by an
honest "could not verify" message instead of a confident guess. Applies to model-written text only, never to code-written answers.
Thresholds are deliberately lenient (we only drop clearly unsupported sentences); measured with eval/answer_eval.py.
"""
import re
from dataclasses import dataclass, field

MARK = re.compile(r"\[(\d+)\]")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
STOP = set("which their there these those would could should about other using where while after before because billed billing "
           "price prices cost costs monthly request requests please sources source provided information based following".split())
PROVIDER_WORDS = {"ola": "Ola Maps", "google": "Google Maps Platform", "mapbox": "Mapbox", "here": "HERE",
                  "nominatim": "OSM Nominatim", "osrm": "OSRM"}
MIN_OVERLAP = 0.20            # share of the sentence's content words found in the cited passages (eval metric uses 0.30)
MIN_NUMBER = 10               # numbers below this are treated as ordinals/small counts and not checked

UNGROUNDED = ("I found sources on this topic, but I couldn't verify a specific answer from them, so I won't guess. Please check the "
              "sources linked below, rephrase the question, or ask about pricing, limits, errors or policies for the supported providers.")
REMOVED_NOTE = "(Some statements could not be verified against the cited sources and were removed.)"


_TRAIL = re.compile(r"([.!?])\s*((?:\[\d+\]\s*)+)(?=\s|$)")


def normalize_markers(text: str) -> str:
    """Models sometimes put the citation AFTER the full stop ("... worldwide. [1]"); the marker would then look like a separate sentence
    and the claim before it would escape the check. Move it before the stop: "... worldwide [1]."."""
    return _TRAIL.sub(lambda m: " " + m.group(2).strip() + m.group(1), text)


def _f(s: str) -> float | None:
    try:
        return float(s.replace(",", "").rstrip("."))
    except ValueError:
        return None


def numbers_in(text: str) -> set[float]:
    return {x for x in (_f(m) for m in NUM.findall(text)) if x is not None}


def calc_numbers(tool_calls: list[dict]) -> set[float]:
    out: set[float] = set()
    for t in tool_calls or []:
        for r in t.get("output", []) or []:
            for k in ("cost", "cost_inr", "units", "free_units", "billable_units", "contact_sales_units"):
                v = _f(str(r.get(k, "")))
                if v is not None:
                    out.add(v)
    return out


@dataclass
class Result:
    text: str
    removed: list[str] = field(default_factory=list)
    cited_total: int = 0
    cited_kept: int = 0
    ungrounded: bool = False


def sentence_supported(sentence: str, chunks: list[dict], allowed: set[float]) -> bool:
    claim = MARK.sub("", sentence)
    blob = " ".join(c["text"] for c in chunks)
    have = numbers_in(blob)
    for n in numbers_in(claim):
        if n < MIN_NUMBER or any(abs(n - a) < 0.011 for a in allowed):
            continue
        if not any(abs(n - h) < 0.011 for h in have):
            return False                                    # a number the sources do not contain
    # "here" is ordinary English ("as shown here"): the provider HERE only counts when written in capitals
    named = {v for k, v in PROVIDER_WORDS.items()
             if (re.search(r"\bHERE\b", claim) if k == "here" else re.search(rf"\b{k}\b", claim.lower()))}
    if named and not any(c["provider"] in named or c["provider"] == "Stack Overflow" for c in chunks):
        return False                                        # talks about a provider none of the cited passages are from
    words = {w for w in re.findall(r"[a-z]{5,}", claim.lower()) if w not in STOP}
    if len(words) >= 4:
        have_words = set(re.findall(r"[a-z]{5,}", blob.lower()))
        if len(words & have_words) / len(words) < MIN_OVERLAP:
            return False
    return True


def verify(text: str, hits: list[dict], allowed_numbers: set[float] | None = None) -> Result:
    """`hits` is the numbered source list the model saw ([n] -> hits[n-1]). Returns the checked text."""
    allowed = allowed_numbers or set()
    text = normalize_markers(text)
    res = Result(text=text)
    out_lines, in_code = [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            out_lines.append(line)
            continue
        if in_code or not MARK.search(line):
            out_lines.append(line)                          # code blocks and uncited lines are not judged here
            continue
        kept = []
        for sent in re.split(r"(?<=[.!?])\s+", line):
            marks = [int(m) for m in MARK.findall(sent)]
            chunks = [hits[n - 1] for n in marks if 1 <= n <= len(hits)]
            if not chunks:
                kept.append(sent)
                continue
            res.cited_total += 1
            if sentence_supported(sent, chunks, allowed):
                res.cited_kept += 1
                kept.append(sent)
            else:
                res.removed.append(sent.strip())
        if kept:
            out_lines.append(" ".join(kept))
        elif not line.strip():
            out_lines.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    if res.cited_total and res.cited_kept == 0:
        res.ungrounded = True
        res.text = UNGROUNDED
    else:
        res.text = cleaned + (f"\n\n{REMOVED_NOTE}" if res.removed else "")
    return res
