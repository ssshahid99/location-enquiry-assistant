"""Mechanical GT v2.5 → schema mapper (GT v2.6 gold rules). Read-only on the GT file.

Purpose: turn each row of the GT workbook (sheet ``Data`` plus the citations
sheet) into one copilot output envelope that follows ``schemas/``.
Use case: imported by ``validator/validate_spec.py`` (``map_all``) to build the
gold expected outputs; it has no command-line entry point of its own.
Example: ``map_record`` maps GT-LBS-DIR-011 to an envelope with
``tools_expected=["doc_search", "calculator"]``, ``step_count_limit=10`` and
``approx_output_tokens=344`` (``ceil(chars/4)``).

GT v2.6 gold rules (approved by Shahid 2026-11-16 14:03 IST; see
``docs/gt-v2.6-rules.md``): confirmed fixes CF1-CF4 and rules R1-R14 in the
qualification extractors below, plus the label overrides L1/L2 read from
``mapping/label_overrides.json``. The raw GT is never edited.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
VOCAB_PATH = REPO / "vocab" / "controlled-vocabulary.json"
DEFAULT_GT = Path("/workspace/capstone-trackb/data/gt_v2_5.xlsx")

LABEL_OVERRIDES_PATH = REPO / "mapping" / "label_overrides.json"

# v2.3 applied the approved tool re-labels and provider-label merge in the
# GT workbook. v2.4 keeps those values, relabels the remaining bare Support
# owners, fills the two previously empty tool traces, and adds blank
# self-review columns. The mapper copies expected_tools, service_providers,
# owners and self-review fields as they stand. v2.5 adds 11 eval records and
# swaps 16 frozen-core members (frozen size stays 80) with the same columns.
# eval-freeze-v2: qualification extraction reads [user] turns only and uses
# stricter timeline, monthly_volume and current_provider rules (see
# user_text, extract_timeline, extract_monthly_volume, extract_current_provider).
# GT v2.6 (eval-freeze-v3, pending tag): CF1-CF4 fix the 9 confirmed frozen-80
# qualification defects, R1-R14 settle the rule boundaries, and L1/L2 relabel two
# records through mapping/label_overrides.json (apply_label_overrides). A missing
# overrides file, or an override whose ``from`` values do not match the GT row,
# fails the run instead of being skipped.

TOOL_ORDER = ["doc_search", "calculator", "update_record", "escalate_ticket"]

COMPARE_INTENTS = {"pricing_comparison", "capability_match", "coverage_limits"}
COMPARE_BUCKETS = {
    "pricecompare",
    "capabilitycompare",
    "coveragecompare",
    "limitscompare",
}

PROVIDER_ALIASES = {
    "google": "Google Maps Platform",
    "google maps": "Google Maps Platform",
    "google maps platform": "Google Maps Platform",
    "ola": "Ola Maps",
    "ola maps": "Ola Maps",
    "mapbox": "Mapbox",
    "nominatim": "OSM Nominatim",
    "osm nominatim": "OSM Nominatim",
    "osm": "OSM Nominatim",
    "osrm": "OSRM",
    "osrm (openstreetmap)": "OSRM",
    "mappls": "Mappls (MapmyIndia)",
    "mapmyindia": "Mappls (MapmyIndia)",
    "mappls (mapmyindia)": "Mappls (MapmyIndia)",
}


def load_vocab(path: Path = VOCAB_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def approx_output_tokens(text: str) -> int:
    """APPROVED interim method: ceil(Unicode character count / 4).

    This is not the Qwen/Qwen3-8B tokenizer. Official counts will be
    measured with the hosted endpoint usage field in C10/eval. The
    endpoint is not called from this activity.
    """
    n = len(text or "")
    return math.ceil(n / 4) if n else 0


def parse_escalation(raw: Any) -> dict:
    out = {
        "should_escalate": None,
        "severity": None,
        "owner": None,
        "reason": None,
    }
    for part in str(raw or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    flag = out["should_escalate"]
    if flag is not None:
        out["should_escalate"] = str(flag).strip().lower() == "true"
    return out



def as_text(raw: Any) -> str:
    """Return a string, treating null/NaN as empty. Does not invent content."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or (isinstance(raw, str) is False and pd.isna(raw)):
        return ""
    return str(raw)


def optional_blank(raw: Any) -> str | None:
    """Copy a GT self-review field; blank/NaN becomes None, which is allowed until review."""
    value = as_text(raw).strip()
    return value or None


def split_csv(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]


def canon_providers(raw: Any, vocab: dict) -> tuple[list[str], list[str]]:
    merges = vocab.get("provider_merges") or {}
    allowed = set(vocab["providers"])
    out: list[str] = []
    unknown: list[str] = []
    for part in split_csv(raw):
        part = merges.get(part, part)
        if part not in allowed:
            unknown.append(part)
            continue
        if part not in out:
            out.append(part)
    return out, unknown


def mapped_tools(record_id: str, raw: Any, vocab: dict) -> tuple[list[str], list[str], list[str]]:
    allowed = set(vocab["tools"])
    raw_list = split_csv(raw)
    unknown = [t for t in raw_list if t not in allowed]
    mapped = [t for t in TOOL_ORDER if t in raw_list]
    return mapped, raw_list, unknown


def parse_clarifying(raw: Any) -> dict:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or not str(raw).strip():
        return {"needed": False, "questions": []}
    questions = []
    for line in str(raw).splitlines():
        line = line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        elif line.startswith("-"):
            line = line[1:].strip()
        if line:
            questions.append({"text": line, "field": None})
    return {"needed": bool(questions), "questions": questions}


def parse_citations_from_sheet(record_id: str, cites: pd.DataFrame | None) -> list[dict]:
    if cites is None or cites.empty:
        return []
    rows = cites[cites["id"] == record_id]
    items = []
    for _, r in rows.iterrows():
        qos = r.get("quote_or_summary")
        if pd.isna(qos) if not isinstance(qos, str) else False:
            qos = None
        if qos not in ("verbatim", "summary"):
            qos = None
        url = "" if pd.isna(r.get("doc_url")) else str(r.get("doc_url"))
        span = "" if pd.isna(r.get("span")) else str(r.get("span"))
        claim = "" if pd.isna(r.get("supports_claim")) else str(r.get("supports_claim"))
        if not url and not span:
            continue
        items.append(
            {
                "url": url or "unknown",
                "span": span or url or "see source",
                "supports_claim": claim or "supporting span",
                "quote_or_summary": qos,
            }
        )
    return items


CITATION_LINE = re.compile(
    r"\[(\d+)\]\s+(\S+)\s+\|\s+(.*?)\s+\|\s+supports:\s+(.*?)(?:\s*\((verbatim|summary)\))?\s*$",
    re.I,
)


def parse_citations_from_text(raw: Any) -> list[dict]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    items = []
    for line in str(raw).splitlines():
        line = line.strip()
        if not line:
            continue
        m = CITATION_LINE.search(line)
        if not m:
            # looser split on pipes after the leading [n] url
            m2 = re.match(r"\[\d+\]\s+(\S+)\s+\|\s+(.*)$", line)
            if not m2:
                continue
            url = m2.group(1)
            rest = m2.group(2)
            parts = [p.strip() for p in rest.split("|")]
            span = parts[0] if parts else ""
            claim = parts[1] if len(parts) > 1 else ""
            qos = None
            if "verbatim" in claim.lower():
                qos = "verbatim"
            elif "summary" in claim.lower():
                qos = "summary"
            claim = re.sub(r"\s*\((verbatim|summary)\)\s*$", "", claim, flags=re.I)
            if claim.lower().startswith("supports:"):
                claim = claim.split(":", 1)[1].strip()
            items.append(
                {
                    "url": url,
                    "span": span or url,
                    "supports_claim": claim or "supporting span",
                    "quote_or_summary": qos,
                }
            )
            continue
        items.append(
            {
                "url": m.group(2),
                "span": m.group(3).strip() or m.group(2),
                "supports_claim": (m.group(4) or "supporting span").strip(),
                "quote_or_summary": (m.group(5) or "").lower() or None,
            }
        )
    return items


def parse_update_proposal(trace: Any, answer: Any) -> dict | None:
    text = as_text(trace)
    m = re.search(r"update_record\s+(\{.*?\})\s*->", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            fields = obj.get("fields") or {}
            return {
                "record_id": str(obj.get("record_id") or "").strip(),
                "mode": obj.get("mode") or "propose",
                "fields": fields,
            }
        except json.JSONDecodeError:
            pass
    # Fallback: "Proposed sandbox update for TKT-123 (...): k=v; k=v."
    ans = as_text(answer)
    m2 = re.search(
        r"Proposed sandbox update for\s+(\S+)\s+\([^)]*\):\s*(.+)$",
        ans,
        re.I | re.S,
    )
    if not m2:
        return None
    record_id = m2.group(1).rstrip(".")
    fields = {}
    for part in re.split(r";", m2.group(2)):
        part = part.strip().rstrip(".")
        if "=" in part:
            k, v = part.split("=", 1)
            fields[k.strip()] = v.strip()
    return {"record_id": record_id, "mode": "propose", "fields": fields}


def parse_escalate_payload(trace: Any) -> dict | None:
    text = as_text(trace)
    m = re.search(r"escalate_ticket\s+(\{.*?\})\s*->", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


_TURN_TAG = re.compile(r"^\s*\[(user|assistant)\]\s*", re.I)


def user_text(question: str) -> str:
    """Return only the user-authored text of a GT question.

    Questions with ``[user]`` / ``[assistant]`` line tags keep only the user
    turns (continuation lines belong to the preceding turn). Untagged
    questions are treated as user text in full.
    """
    q = question or ""
    lines = q.splitlines()
    if not any(_TURN_TAG.match(line) for line in lines):
        return q
    keep: list[str] = []
    role = "user"
    for line in lines:
        m = _TURN_TAG.match(line)
        if m:
            role = m.group(1).lower()
            line = line[m.end():]
        if role == "user":
            keep.append(line)
    return "\n".join(keep)


_MULTIPLIERS = {
    "k": 1_000,
    "thousand": 1_000,
    "m": 1_000_000,
    "mn": 1_000_000,
    "million": 1_000_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
}


def _parse_volume_number(raw: str) -> int | None:
    """Parse '1,500,000', '50M', '2.5 lakh', '1 crore', '200k' into an integer."""
    s = raw.replace(",", "").strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([a-z]+)?$", s)
    if not m:
        return None
    value = float(m.group(1))
    suffix = m.group(2)
    if suffix:
        if suffix not in _MULTIPLIERS:
            return None
        value *= _MULTIPLIERS[suffix]
    return int(round(value))


_VOLUME_NUMBER = (
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?:\s*(?:k|thousand|M|mn|million|lakhs?|lac|crores?)\b)?)"
)
_VOLUME_UNITS = (
    r"(?P<unit>requests|request|calls|call|lookups|lookup|sessions|session|elements|"
    r"element|searches|search|queries|query|transactions|transaction)"
)
# CF1: up to 5 API-phrase tokens from a closed list (provider/product words or a
# short parenthesised gloss) may sit between the number and the unit, so
# "route (directions) requests", "Google Places Autocomplete Requests" and
# "Ola Directions calls" match. (eval-freeze-v2 allowed one API word.)
_VOLUME_API_WORDS = (
    r"(?:(?:API|Google|Maps|Platform|Ola|Mapbox|Mappls|MapmyIndia|Nominatim|OSRM|"
    r"Places?|Autocomplete|Directions|Distance|Matrix|Routes?|routing|Geocoding|"
    r"Reverse[- ]Geocod\w*|Search|Box|\([A-Za-z][A-Za-z -]{0,30}\))\s+){0,5}"
)
_VOLUME_PER = r"(?:a\s+month|per\s+month|monthly|each\s+month|every\s+month|/\s*month|/\s*mo\b)"
VOLUME_PATTERNS = [
    # "2,000,000 Directions requests a month", "6,000,000 route (directions) requests a month"
    re.compile(_VOLUME_NUMBER + r"\s+" + _VOLUME_API_WORDS + _VOLUME_UNITS + r"\s*" + _VOLUME_PER, re.I),
    # "20,000 per month", "300,000 a month", "50M/month"
    re.compile(_VOLUME_NUMBER + r"\s*" + _VOLUME_PER, re.I),
]

_UNIT_CANON = {
    "request": "requests", "call": "calls", "lookup": "lookups", "session": "sessions",
    "element": "elements", "search": "searches", "query": "queries",
    "transaction": "transactions",
}


def extract_monthly_volume(text: str) -> dict | None:
    for pat in VOLUME_PATTERNS:
        vm = pat.search(text)
        if not vm:
            continue
        n = _parse_volume_number(vm.group("num"))
        if n is None:
            continue
        unit_word = (vm.groupdict().get("unit") or "").lower()
        unit_word = _UNIT_CANON.get(unit_word, unit_word)
        return {
            "raw": vm.group(0).strip(),
            "requests_per_month": n,
            "unit": f"{unit_word}_per_month" if unit_word else "per_month",
        }
    return None


_MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
)
_TIME_EXPR = (
    r"(?:(?:the\s+)?end\s+of\s+(?:the\s+)?(?:month|quarter|year|" + _MONTHS + r"(?:\s+20\d\d)?|Q[1-4](?:\s+20\d\d)?)"
    r"|Q[1-4](?:\s*(?:FY)?\s*20\d\d)?"
    r"|(?:next|this)\s+(?:week|month|quarter|year)"
    r"|\d+\s+(?:days?|weeks?|months?|years?)"
    r"|" + _MONTHS + r"(?:\s+\d{1,2})?(?:,?\s+20\d\d)?"
    r"|\d{1,2}\s+" + _MONTHS + r"(?:\s+20\d\d)?"
    r"|20\d\d)"
)
TIMELINE_PATTERNS = [
    # deadline preposition + time expression: "by Q3", "within 2 months", "before March 2027"
    re.compile(
        r"\b(?:by|before|within|until|no\s+later\s+than|in)\s+" + _TIME_EXPR + r"\b",
        re.I,
    ),
    # launch / go-live / start / deadline phrasing followed by a time expression
    re.compile(
        r"\b(?:launch\w*|go(?:ing)?[- ]live|go[- ]live|start\w*|deadline|rollout|roll\s+out|migrat\w*|switch\w*)"
        r"[^.?!\n]{0,40}?\b(?P<t>(?:(?:by|before|within|in|on|for)\s+)?(?:(?:the\s+)?end\s+of\s+(?:the\s+)?(?:month|quarter|year)|Q[1-4](?:\s*20\d\d)?|(?:next|this)\s+(?:week|month|quarter|year)|\d+\s+(?:days?|weeks?|months?)))\b",
        re.I,
    ),
    # standalone relative time
    re.compile(r"\b(?:next\s+(?:week|month|quarter|year)|ASAP|as\s+soon\s+as\s+possible)\b", re.I),
]
# "in <Month> <year>" is only a timeline when attached to the user's own plan,
# not to a vendor change ("pricing is changing in September 2026").
_TIMELINE_EXCLUDE = re.compile(r"\b(?:pricing|price|prices|policy|terms)\b[^.?!\n]{0,40}$", re.I)


def extract_timeline(text: str) -> str | None:
    for pat in TIMELINE_PATTERNS:
        for m in pat.finditer(text):
            prefix = text[max(0, m.start() - 60):m.start()]
            if m.group(0).lower().startswith("in ") and _TIMELINE_EXCLUDE.search(prefix):
                continue
            # a bare "in <year>" or "in <month>" is too weak; require by/before/within/until or a plan word
            if re.match(r"(?i)in\s+(?:" + _MONTHS + r"|20\d\d)", m.group(0)):
                continue
            value = m.groupdict().get("t") or m.group(0)
            return value.strip()
    return None


_PROVIDER_NAME = (
    r"(?P<prov>Google\s+Maps\s+Platform|Google\s+Maps|Google|Ola\s+Maps|Ola|Mapbox|"
    r"Mappls\s*\(MapmyIndia\)|Mappls|MapmyIndia|OSRM|OSM\s+Nominatim|Nominatim|"
    r"OpenStreetMap|OSM)"
)
_PROVIDER_ALIAS_EXTRA = {"openstreetmap": "OSM Nominatim"}
# Current-use cues. A provider that is only being evaluated or quoted is not current.
CURRENT_PROVIDER_CUES = [
    ("currently", re.compile(r"\bcurrently\s+(?:use|using|on|run(?:ning)?\s+on|pay(?:ing)?\s+for)?\s*(?:the\s+)?" + _PROVIDER_NAME + r"(?:'s)?\b", re.I)),
    ("we use", re.compile(r"\bwe(?:'re|\s+are)?\s+(?:use|using|run\s+on|running\s+on|rely\s+on|pay\s+for|on)\s+(?:the\s+)?" + _PROVIDER_NAME + r"(?:'s)?\b", re.I)),
    ("migrating/switching from", re.compile(r"\b(?:migrat\w*|switch\w*|mov\w*)\s+(?:away\s+)?(?:off|from)\s+(?:the\s+)?" + _PROVIDER_NAME + r"\b", re.I)),
    ("existing", re.compile(r"\bexisting\s+" + _PROVIDER_NAME + r"\b", re.I)),
    ("our <provider> ...", re.compile(r"\bour\s+" + _PROVIDER_NAME + r"(?:'s)?\b", re.I)),
    ("<provider> customer", re.compile(r"\bwe(?:'re|\s+are)\s+an?\s+(?:[\w-]+\s+){0,3}" + _PROVIDER_NAME + r"\s+customer\b", re.I)),
]
# CF3: "our <modifier> <provider>" with a closed modifier list ("Our self-hosted OSRM").
_OUR_MODIFIERS = r"(?:self-hosted|self-host|own|existing|current|internal|production|paid|on-prem(?:ise)?)"
CURRENT_PROVIDER_CUES.append(
    ("our <modifier> <provider>", re.compile(r"\bour\s+(?:" + _OUR_MODIFIERS + r"\s+){1,2}" + _PROVIDER_NAME + r"(?:'s)?\b", re.I))
)
# R5: explicit first-person present-tense use outside the cue list
# ("Our mobile app calls Nominatim", "we reverse geocode ... with Mapbox",
# "a Mapbox token used by our service"). A commitment is not a usage verb.
CURRENT_PROVIDER_CUES.extend([
    ("our <thing> calls <provider>", re.compile(r"\bour\s+(?:[\w-]+\s+){1,3}(?:calls|uses|queries|hits)\s+(?:the\s+)?" + _PROVIDER_NAME + r"\b", re.I)),
    ("we <geo verb> ... with <provider>", re.compile(r"\bwe\s+(?:reverse[- ]geocode|geocode|route|call|query|serve|power)\b[^.?!\n]{0,50}?\bwith\s+(?:the\s+)?" + _PROVIDER_NAME + r"\b", re.I)),
    ("<provider> ... used by our", re.compile(_PROVIDER_NAME + r"(?:\s+[\w-]+){0,3}\s+used\s+by\s+our\b", re.I)),
])
# R1-R4 keep the eval-freeze-v2 behaviour: possessive "our <provider> ..." and
# "<provider> customer" count; "existing project" counts only with the provider
# named; an incident/error report alone does not set a provider.


def _canon_provider(name: str) -> str | None:
    key = re.sub(r"\s+", " ", name.strip().lower())
    return PROVIDER_ALIASES.get(key) or _PROVIDER_ALIAS_EXTRA.get(key)


# "Can we use X?", "should we use X", "whether we use X" ask about a provider
# under evaluation; they are not current use.
_EVALUATION_PREFIX = re.compile(
    r"\b(?:can|could|should|would|shall|may|might|do|does|did|if|whether|to|why\s+not)\s+$", re.I
)

# R14: the schema enum holds one canonical provider. When the user states current
# use of 2+ different providers, keep the one named next to the record's API;
# otherwise the earliest cue. Compared or migration-target providers never count.
_R14_API_WORDS = {
    "directions": r"direction|rout",
    "distance_matrix": r"matrix|distance|table",
    "autocomplete": r"autocomplete|autosuggest|type-ahead|search",
    "reverse_geocode": r"reverse|geocod",
}


def extract_current_provider(text: str, api: str | None = None) -> tuple[str | None, str | None]:
    """Return (canonical provider, cue name) for the current-use cue, else (None, None)."""
    hits: list[tuple[int, int, str, str]] = []
    for cue, pat in CURRENT_PROVIDER_CUES:
        for m in pat.finditer(text):
            if _EVALUATION_PREFIX.search(text[max(0, m.start() - 20):m.start()]):
                continue
            prov = _canon_provider(m.group("prov"))
            if prov:
                hits.append((m.start(), m.end(), prov, cue))
    if not hits:
        return None, None
    # Earliest cue wins; ties keep the cue-list order (stable sort on start only).
    hits.sort(key=lambda h: h[0])
    if api and len({h[2] for h in hits}) >= 2:
        api_words = _R14_API_WORDS.get(api)
        for st, en, prov, cue in hits:
            if api_words and re.search(api_words, text[max(0, st - 40):en + 40], re.I):
                return prov, cue
    return hits[0][2], hits[0][3]


# CF2: more India phrasings ("for an India-only app", "as well as India"); an
# "India-only" followed by data/residency/hosting is a data-residency
# requirement, not a region (see R9), so it is excluded.
_INDIA_CF2 = (
    r"\b(?:(?:in|across|for|throughout|within)\s+an?\s+India(?:-only|-wide)?\b(?!\s+(?:data|residency|hosting|storage))"
    r"|as\s+well\s+as\s+India\b)"
)
# CF2: closed list of countries stated in GT v2.5 user text (India plus 10 others).
_COUNTRY_RE = r"(?P<c>India|Nepal|Bhutan|Bangladesh|Sri\s+Lanka|Pakistan|Indonesia|Thailand|Singapore|United\s+Arab\s+Emirates|UK)"


def _canon_country(c: str) -> str:
    return re.sub(r"\s+", " ", c)


_COUNTRY_PREP = re.compile(
    r"\b(?:in|across|for|throughout|within)\s+" + _COUNTRY_RE + r"\b(?![-'])",
    re.I,
)


def _first_country(q: str) -> str | None:
    """CF2: first non-India country from the closed list after a location
    preposition (same prepositions as the existing India rule)."""
    for m in _COUNTRY_PREP.finditer(q):
        c = _canon_country(m.group("c"))
        if c != "India":
            return c
    return None


# R10: naming the India price list/charges/free cap states the India region.
_INDIA_PRICE_R10 = r"\bIndia\s+(?:[\w-]+\s+){0,3}(?:charges|pricing|prices?|rates?|free\s+cap|price\s+(?:list|card))\b"


# R7: a country counts when it follows a location preposition or is chained to
# such a country with "and", "," or "as well as" ("in India and Nepal").
_REGION_LIST_R7 = re.compile(
    r"\b(?:in|across|for|throughout|within|cover|covers|covering)\s+" + _COUNTRY_RE.replace("?P<c>", "")
    + r"(?:\s*(?:,|and|as\s+well\s+as)\s+" + _COUNTRY_RE.replace("?P<c>", "") + r")*\b(?![-'])",
    re.I,
)
_COUNTRY_ANY = re.compile(_COUNTRY_RE + r"\b(?![-'])")


def _multi_region(q: str, region: str) -> str:
    """R7: all closed-list countries in a location context, alphabetical,
    joined by ", " (the schema types region as a single string)."""
    found: list[tuple[int, str]] = []
    for lm in _REGION_LIST_R7.finditer(q):
        for cm in _COUNTRY_ANY.finditer(q, lm.start(), lm.end()):
            found.append((cm.start(), _canon_country(cm.group("c"))))
    # Alphabetical order so the value does not depend on mention order.
    names = {c for _pos, c in found} | {region}
    return ", ".join(sorted(names))


# R6: technical constraints = a user-stated requirement on how the API must be
# deployed or called: traffic need, request rate, own self-hosting/on-prem
# deployment, data residency. "no/without traffic" counts wherever stated (as in
# the eval-freeze-v2 regex); the other kinds count only in a declarative
# sentence (not a question), so capabilities asked about ("Which APIs give
# traffic-aware ETAs?", "If we self-host OSRM ...?") are not constraints.
# Request sizes (stops, matrix size), storage/caching, language and SLA asks are
# not included. Values are the verbatim span; several are joined by "; ".
# A stated request rate is a fact about the user's load even inside a question
# ("... at about 5 requests per second?"), so rates count everywhere too.
_TC_ALWAYS = re.compile(
    r"\b(?:no\s+(?:live\s+)?traffic(?:\s+needed)?|without\s+(?:live\s+)?traffic)\b"
    r"|\b\d[\d,]*\s+(?:[\w-]+\s+){0,2}(?:requests|calls|pings|queries|lookups)\s+(?:a|per)\s+second\b|\b\d+\s+(?:rps|qps)\b",
    re.I,
)
_TC_DECLARATIVE = [
    re.compile(r"\btraffic-aware\b", re.I),
    re.compile(r"\b(?:our|we)\s+(?P<sh>self-host(?:ed|ing)?)\b", re.I),
    re.compile(r"\bon-prem(?:ise)?\b", re.I),
    re.compile(r"\b[\w-]+-only\s+data\s+residency\b|\bdata\s+residency\s+in\s+\w+\b", re.I),
]


def _sentence_is_question(q: str, pos: int) -> bool:
    m = re.compile(r"[.?!\n]").search(q, pos)
    return bool(m and m.group(0) == "?")


def _technical_constraints_r6(q: str) -> str | None:
    hits: list[tuple[int, str]] = []
    for m in _TC_ALWAYS.finditer(q):
        hits.append((m.start(), m.group(0).strip()))
    for pat in _TC_DECLARATIVE:
        for m in pat.finditer(q):
            if _sentence_is_question(q, m.end()):
                continue
            val = m.group("sh") if "sh" in pat.groupindex and m.group("sh") else m.group(0)
            hits.append((m.start(), val.strip()))
    out: list[str] = []
    for _pos, h in sorted(hits):
        if h.lower() not in [o.lower() for o in out]:
            out.append(h)
    return "; ".join(out) if out else None


# R12: a named person/role/team who decides, approves or signs off.
_DECISION_MAKER_R12 = re.compile(
    r"\b(?:our|the)\s+(?P<who>(?:CTO|CEO|CFO|COO|founder|VP\s+[\w ]{2,30}?|head\s+of\s+[\w ]{2,30}?|"
    r"procurement(?:\s+team)?|finance(?:\s+team)?|legal(?:\s+team)?|[\w-]+\s+manager))\s+"
    r"(?:decides|will\s+decide|must\s+approve|approves|will\s+approve|signs?\s+off|has\s+the\s+final\s+say)\b",
    re.I,
)


def extract_qualification(question: str, api: str, vocab: dict) -> dict:
    # User turns only: assistant lines in history-format questions are never mined.
    q = user_text(question or "")
    use_case = None
    # CF4: no length cap (the old 80-character cap cut AC-234 mid-word).
    m = re.search(r"\bWe are an?\s+([^.?\n]{3,})", q)
    if m is None:
        # R13: other self-descriptions: "We're a/an X", "We run a/an X"; the
        # noun phrase stops before a following "and <verb>" clause.
        m = re.search(r"\b(?:We're|We\s+run)\s+an?\s+((?:(?!\s+and\s+(?:expect|need|want|plan|are|use)\b)[^.?\n]){3,})", q)
    if m:
        use_case = m.group(1).strip().rstrip(",")
    if use_case is None:
        m = re.search(
            r"\bOur\s+((?:commercial\s+)?(?:delivery|logistics|ride[- ]?hailing|"
            r"navigation|fleet|dispatch)[^.,?\n]{0,60})",
            q,
            re.I,
        )
        if m:
            use_case = m.group(0)[4:].strip()  # drop 'Our '

    # region: India phrasings, then CF2 other countries, R10 India price list,
    # R7 several countries; R8 (city only), R9 (data residency) and R11
    # ("global") set no region.
    region = None
    if re.search(r"\b(?:in|across|for|throughout)\s+India\b", q, re.I):
        region = "India"
    elif re.search(r"\bIndian\s+(?:cities|city|market|price|pricing)\b", q, re.I):
        region = "India"
    elif re.search(r"\bIndia(?:n)?\s+price list\b", q, re.I):
        region = "India"
    elif re.search(_INDIA_CF2, q, re.I):
        region = "India"
    if region is None:
        region = _first_country(q)
    if region is None and re.search(_INDIA_PRICE_R10, q, re.I):
        region = "India"
    if region is not None:
        region = _multi_region(q, region)

    monthly_volume = extract_monthly_volume(q)

    current_provider, _cue = extract_current_provider(q, api)

    timeline = extract_timeline(q)

    budget = None
    bm = re.search(
        r"\b(?:budget(?:\s+of)?|we can spend|no more than|under)\s+"
        r"(₹\s*[\d,.]+|USD\s*[\d,.]+|\$\s*[\d,.]+|[₹$][\d,.]+)",
        q,
        re.I,
    )
    if bm:
        budget = bm.group(0).strip()

    technical_constraints = _technical_constraints_r6(q)

    decision_maker = None
    dmm = _DECISION_MAKER_R12.search(q)
    if dmm:
        decision_maker = dmm.group("who").strip()

    use_case_api = {"api": api, "use_case": use_case}
    required = vocab["qualification_required_fields"]
    values = {
        "use_case_api": use_case_api,
        "region": region,
        "monthly_volume": monthly_volume,
        "current_provider": current_provider,
        "timeline": timeline,
        "budget": budget,
        "technical_constraints": technical_constraints,
        "decision_maker": decision_maker,
    }
    missing = [f for f in required if values[f] is None]
    values["missing_required_fields"] = missing
    values["complete"] = len(missing) == 0
    return values


def comparison_dimensions(intent: str, sub_intent: str, bucket: str) -> list[str]:
    dims: list[str] = []

    def add(d: str) -> None:
        if d not in dims:
            dims.append(d)

    if intent in {"pricing_quote", "pricing_comparison"} or bucket == "pricecompare":
        add("pricing")
    if intent in {"capability_match", "sales_qualification"} or bucket == "capabilitycompare":
        add("capabilities")
    if intent == "coverage_limits" or bucket in {"coveragecompare", "limitscompare"}:
        if sub_intent == "limits_question" or bucket == "limitscompare":
            add("limits")
        if sub_intent == "coverage_question" or bucket == "coveragecompare":
            add("coverage")
        if sub_intent == "performance_question":
            add("limits")
        if intent == "coverage_limits" and not dims:
            add("coverage")
            add("limits")
    if bucket == "capabilitycompare" and not dims:
        add("capabilities")
    if not dims:
        add("capabilities")
    return dims


def map_record(row: pd.Series, vocab: dict, cites: pd.DataFrame | None) -> tuple[dict, list[str]]:
    issues: list[str] = []
    rid = str(row["id"])
    intent = str(row["intent"])
    sub_intent = str(row["sub_intent"])
    api = str(row["api"])
    team = str(row["primary_team"])
    bucket = str(row["bucket"])
    question = "" if pd.isna(row.get("question")) else str(row.get("question"))
    answer = "" if pd.isna(row.get("reference_answer")) else str(row.get("reference_answer"))
    if not answer.strip():
        issues.append("empty_reference_answer")
        answer = "(empty gold reference_answer)"

    # Intent mapping check
    expected_intent = None
    for parent, kids in vocab["intent_to_sub_intents"].items():
        if sub_intent in kids:
            expected_intent = parent
            break
    if expected_intent is None:
        issues.append(f"unmapped_sub_intent:{sub_intent}")
    elif expected_intent != intent:
        issues.append(f"intent_mismatch:gt={intent},from_sub={expected_intent}")

    providers, unknown_p = canon_providers(row.get("service_providers"), vocab)
    if unknown_p:
        issues.append("unknown_providers:" + ",".join(unknown_p))

    tools, tools_raw, tool_issues = mapped_tools(rid, row.get("expected_tools"), vocab)
    issues.extend([f"tools:{t}" for t in tool_issues])

    esc = parse_escalation(row.get("escalation"))
    if esc["owner"] not in vocab["owners"]:
        issues.append(f"unknown_owner:{esc['owner']}")
    if esc["severity"] not in vocab["severities"]:
        issues.append(f"unknown_severity:{esc['severity']}")
    if esc["should_escalate"] is None:
        issues.append("missing_should_escalate")
        esc["should_escalate"] = False
    if not esc["reason"]:
        issues.append("missing_escalation_reason")
        esc["reason"] = "No reason recorded in GT escalation field."
    if esc["owner"] is None:
        esc["owner"] = "Support L1" if team == "Support" else "Account team (Sales)"
    if esc["severity"] is None:
        esc["severity"] = "SEV4"

    citations = parse_citations_from_sheet(rid, cites)
    if not citations:
        citations = parse_citations_from_text(row.get("cited_doc_spans"))

    clarifying = parse_clarifying(row.get("gold_clarifying_questions"))
    qualification = extract_qualification(question, api, vocab)

    proposal = parse_update_proposal(row.get("gold_tool_trace"), answer)
    update_applicable = "update_record" in tools or proposal is not None
    if "update_record" in tools and proposal is None:
        issues.append("update_record_expected_but_unparsed")
        update_applicable = False
    if update_applicable and proposal:
        # Normalise provider label inside fields
        if "providers" in proposal["fields"]:
            merged, unk = canon_providers(proposal["fields"]["providers"], vocab)
            if merged:
                proposal["fields"]["providers"] = ", ".join(merged)
            if unk:
                issues.append("update_providers_unknown:" + ",".join(unk))
        if proposal.get("mode") != "propose":
            issues.append(f"update_mode:{proposal.get('mode')}")
            proposal["mode"] = "propose"
        if not proposal.get("record_id"):
            issues.append("update_missing_record_id")
            update_applicable = False
            proposal = None

    if update_applicable and proposal:
        record_update = {
            "applicable": True,
            "requires_human_approval": True,
            "approval_status": "pending",
            "executed": False,
            "proposal": proposal,
        }
    else:
        record_update = {
            "applicable": False,
            "requires_human_approval": False,
            "approval_status": "not_applicable",
            "executed": False,
            "proposal": None,
        }

    compare_applicable = (
        intent in COMPARE_INTENTS
        or bucket in COMPARE_BUCKETS
        or (len(providers) >= 2 and intent in {"pricing_quote", "sales_qualification"})
    )
    if compare_applicable:
        comparison = {
            "applicable": True,
            "providers": providers or [],
            "dimensions": comparison_dimensions(intent, sub_intent, bucket),
            "equivalent_requirements": {
                "api": api,
                "region": qualification.get("region"),
                "monthly_volume_raw": (
                    qualification["monthly_volume"]["raw"]
                    if qualification.get("monthly_volume")
                    else None
                ),
            },
            "assumptions": [],
            "source_urls": list(dict.fromkeys(c["url"] for c in citations if c.get("url"))),
            "notes": None,
        }
        if not comparison["providers"]:
            issues.append("comparison_applicable_without_providers")
            comparison["applicable"] = False
            comparison["providers"] = []
            comparison["dimensions"] = []
    else:
        comparison = {
            "applicable": False,
            "providers": [],
            "dimensions": [],
            "equivalent_requirements": None,
            "assumptions": [],
            "source_urls": [],
            "notes": None,
        }

    if esc["should_escalate"] or "escalate_ticket" in tools:
        action_type = "propose_escalation"
        requires_approval = True
        approval_status = "pending"
        payload = parse_escalate_payload(row.get("gold_tool_trace"))
        aliases = vocab.get("owner_role_aliases", {})
        esc_block = {
            "severity": esc["severity"],
            "owner": esc["owner"],
            "summary": None,
        }
        if payload:
            if payload.get("severity"):
                esc_block["severity"] = payload["severity"]
            if payload.get("owner_role"):
                esc_block["owner"] = aliases.get(payload["owner_role"], esc["owner"])
            if payload.get("summary"):
                esc_block["summary"] = payload["summary"]
        description = esc["reason"]
        next_action = {
            "action_type": action_type,
            "description": description,
            "requires_human_approval": requires_approval,
            "approval_status": approval_status,
            "executed": False,
            "escalation": esc_block,
        }
    elif update_applicable:
        next_action = {
            "action_type": "propose_record_update",
            "description": (
                f"Propose a sandbox update for {proposal['record_id']} "
                "and hold it for human approval."
            ),
            "requires_human_approval": True,
            "approval_status": "pending",
            "executed": False,
            "escalation": None,
        }
    else:
        next_action = {
            "action_type": "answer_directly",
            "description": "Deliver the grounded draft reply.",
            "requires_human_approval": False,
            "approval_status": "not_applicable",
            "executed": False,
            "escalation": None,
        }

    envelope = {
        "schema_version": "1.0.0",
        "record_id": rid,
        "intent": intent,
        "sub_intent": sub_intent,
        "api": api,
        "team": team,
        "bucket": bucket,
        "tools_expected": tools,
        "tools_raw_gt": tools_raw,
        "step_count_limit": 10,
        "output_token_budget": 1024,
        "approx_output_tokens": approx_output_tokens(answer),
        "split": str(row.get("split")),
        "frozen_core": bool(row.get("frozen_core")),
        "self_review_date": optional_blank(row.get("self_review_date")),
        "self_review_evidence": optional_blank(row.get("self_review_evidence")),
        "outputs": {
            "response_draft": {
                "draft_text": answer,
                "citations": citations,
                "is_refusal": False,
                "refusal": None,
            },
            "clarifying_questions": clarifying,
            "qualification_details": qualification,
            "proposed_record_update": record_update,
            "comparison": comparison,
            "responsible_role": {
                "owner": esc["owner"],
                "team": team,
                "should_escalate": bool(esc["should_escalate"]),
                "severity": esc["severity"],
                "reason": esc["reason"],
            },
            "next_action": next_action,
        },
    }
    return envelope, issues


def load_gt(path: Path = DEFAULT_GT) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = pd.read_excel(path, sheet_name="Data")
    try:
        cites = pd.read_excel(path, sheet_name="citations")
    except ValueError:
        cites = pd.DataFrame()
    return data, cites


class LabelOverrideError(RuntimeError):
    """label_overrides.json is missing, or an override does not match the GT row."""


def load_label_overrides(path: Path = LABEL_OVERRIDES_PATH) -> dict:
    """Approved label corrections (GT v2.6 L1/L2), keyed by record_id.

    The file is required. A missing file raises ``LabelOverrideError`` instead
    of returning no overrides, which would silently drop L1 and L2.
    """
    if not path.exists():
        raise LabelOverrideError(
            f"label overrides file is required and was not found: {path}"
        )
    return json.loads(path.read_text(encoding="utf-8"))["overrides"]


_OVERRIDE_FIELDS = {"intent", "sub_intent", "primary_team", "api"}


def apply_label_overrides(row: pd.Series, overrides: dict, vocab: dict) -> tuple[pd.Series, list[str]]:
    """Return the row with approved label overrides applied (the GT file is not edited).

    Each override names the GT values it expects (``from``) and the new ones
    (``to``). A value that no longer matches the GT, a value outside the
    vocabulary or a sub_intent outside its intent raises ``LabelOverrideError``.
    The override is not applied and is not reported as a mapping warning.
    Everything the mapper derives from labels (comparison, responsible_role.team,
    default owner) then follows the overridden row.
    """
    ov = overrides.get(str(row["id"]))
    if not ov:
        return row, []
    issues: list[str] = []
    for col, old in ov["from"].items():
        if str(row[col]) != old:
            issues.append(f"label_override_stale:{col}:gt={row[col]},expected={old}")
    to = ov["to"]
    bad = [c for c in to if c not in _OVERRIDE_FIELDS]
    intent = to.get("intent", str(row["intent"]))
    sub = to.get("sub_intent", str(row["sub_intent"]))
    if (bad or intent not in vocab["intents"] or sub not in vocab["intent_to_sub_intents"].get(intent, [])
            or to.get("primary_team", "Sales") not in vocab["teams"] or to.get("api", "directions") not in vocab["apis"]):
        issues.append("label_override_invalid")
    if issues:
        raise LabelOverrideError(
            f"{row['id']}: label override is stale or broken and was not applied: "
            + "; ".join(issues)
        )
    row = row.copy()
    for col, val in to.items():
        row[col] = val
    return row, []


def map_all(gt_path: Path = DEFAULT_GT, vocab: dict | None = None) -> tuple[list[dict], list[dict]]:
    vocab = vocab or load_vocab()
    data, cites = load_gt(gt_path)
    overrides = load_label_overrides()
    envelopes = []
    issue_rows = []
    for _, row in data.iterrows():
        row, ov_issues = apply_label_overrides(row, overrides, vocab)
        env, issues = map_record(row, vocab, cites)
        issues = ov_issues + issues
        envelopes.append(env)
        for iss in issues:
            issue_rows.append({"record_id": env["record_id"], "issue": iss})
    return envelopes, issue_rows
