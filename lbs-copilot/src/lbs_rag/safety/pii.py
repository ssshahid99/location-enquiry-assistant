"""PII and secret redaction, applied to user input before it is stored or sent to the model.

Structured identifiers use strict regexes (reliable, and they leave pricing numbers like 3,000,000 alone).
Person names are optional (names=True) via Presidio + spaCy, which loads a large model, so it is off by default.
"""
import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("API_KEY", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),                                   # Google
    ("API_KEY", re.compile(r"\b[ps]k\.[A-Za-z0-9_\-\.]{20,}")),                                # Mapbox tokens
    ("API_KEY", re.compile(r"\b(?:sk|pk|api|key)[-_](?=[A-Za-z_\-]*\d)[A-Za-z0-9_\-]{16,}\b", re.I)),     # needs a digit: slugs like api-security-best-practices are not keys
    ("API_KEY", re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|client[_-]?secret)(\s*[=:]\s*['\"]?)[A-Za-z0-9_\-\.]{16,}")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    # Indian mobile numbers as people write them: 9876543210, 98765 43210, 98765-43210, +91 98765 43210, +91-9876543210, 0091 98765 43210
    ("PHONE", re.compile(r"(?<![\d,.])(?:(?:\+|00)91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d,.]|\s?\d)")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")


def _luhn(s: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", s)]
    if not 13 <= len(d) <= 16:
        return False
    tot = 0
    for i, x in enumerate(reversed(d)):
        if i % 2:
            x = x * 2 - 9 if x * 2 > 9 else x * 2
        tot += x
    return tot % 10 == 0


PUBLIC_EMAILS = ("maps-support@olakrutrim.com",)     # vendor support addresses that answers are meant to show


def redact_with_report(text: str, *, names: bool = False, allow_emails: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    found: list[str] = []
    out = _CARD.sub(lambda m: (found.append("CARD") or "[CARD]") if _luhn(m.group(0)) else m.group(0), text)  # before Aadhaar
    for label, pat in _PATTERNS:
        def sub(m, label=label):
            if label == "EMAIL" and m.group(0).lower() in allow_emails:
                return m.group(0)
            found.append(label)
            if label == "API_KEY" and m.lastindex and m.lastindex >= 2:       # keep "api_key=" so the question still reads
                return f"{m.group(1)}{m.group(2)}[REDACTED_KEY]"
            return f"[{label}]"
        out = pat.sub(sub, out)
    if names:
        out = _redact_names(out, found)
    return out, found


def _redact_names(text: str, found: list[str]) -> str:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    global _AN, _ANON
    try:
        _AN
    except NameError:
        _AN, _ANON = AnalyzerEngine(), AnonymizerEngine()
    res = _AN.analyze(text=text, entities=["PERSON"], language="en", score_threshold=0.6)
    if res:
        found.extend(["PERSON"] * len(res))
        return _ANON.anonymize(text=text, analyzer_results=res).text
    return text


def redact(text: str, *, names: bool = False) -> str:
    return redact_with_report(text, names=names)[0]
