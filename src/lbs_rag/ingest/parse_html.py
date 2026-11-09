"""HTML -> (title, sections[{section, text}]) as markdown-ish text.

Own converter instead of trafilatura: trafilatura strips callouts/notes as boilerplate (e.g. Google's "As of October 2026 ...
can no longer be enabled" box), and those are exactly the facts policy/migration questions need. We keep every block of the
main content (headings, paragraphs, lists, callouts, tables, code) and drop only page chrome.
"""
import re

from bs4 import BeautifulSoup, NavigableString, Tag

_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
# "aside" is kept on purpose: Google puts notes/warnings/deprecations in <aside class="note|caution|special">.
# Page chrome sidebars sit outside <main>/<article>, which _container() already excludes.
_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "form", "button", "iframe", "template"}
_SKIP_CLASS = re.compile(r"(devsite-(header|footer|nav|book-nav|toc|banner-top|snackbar|feedback|thumb|bottom|cookie|language)|"
                         r"cookie|breadcrumb|skip-to|devsite-top|feedback|site-footer)", re.I)
_BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "blockquote", "details", "summary", "dl", "dt", "dd",
          "figure", "figcaption", "tbody", "thead", "tr", "td", "th", "header", "center", "devsite-content"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def _table_md(tb: Tag) -> str:
    rows = []
    for tr in tb.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _walk(node: Tag, out: list[str], buf: list[str]) -> None:
    def flush():
        t = re.sub(r"\s+", " ", " ".join(buf)).strip()
        buf.clear()
        if t:
            out.append(t)

    for ch in node.children:
        if isinstance(ch, NavigableString):
            if not isinstance(ch, type(ch)) or ch.__class__.__name__ in ("Comment", "Doctype"):
                continue
            buf.append(str(ch))
            continue
        if not isinstance(ch, Tag):
            continue
        name = (ch.name or "").lower()
        cls = " ".join(ch.get("class", []))
        if name in _SKIP or (cls and _SKIP_CLASS.search(cls)) or ch.get("hidden") is not None or ch.get("aria-hidden") == "true":
            continue
        if name in _HEADINGS:
            flush()
            t = re.sub(r"\s+", " ", ch.get_text(" ", strip=True))
            if t:
                out.append("#" * _HEADINGS[name] + " " + t)
        elif name == "table":
            flush()
            md = _table_md(ch)
            if md:
                out.append(md)
        elif name == "pre":
            flush()
            out.append("```\n" + ch.get_text() .strip("\n") + "\n```")
        elif name == "li":
            flush()
            buf.append("-")
            _walk(ch, out, buf)
            flush()
        elif name in _BLOCK:
            flush()
            _walk(ch, out, buf)
            flush()
        elif name == "br":
            flush()
        else:                                    # inline: a, span, code, b, em, ...; may still wrap blocks (custom elements)
            if ch.find(list(_BLOCK | set(_HEADINGS) | {"table", "pre", "li"})):
                flush()
                _walk(ch, out, buf)
                flush()
            else:
                buf.append(ch.get_text(" ", strip=True))
    flush()


def _container(soup: BeautifulSoup) -> Tag:
    cands = [t for t in soup.find_all(["article", "main"]) if len(t.get_text(strip=True)) > 400]
    cands += soup.select("[role=main], devsite-content")
    best = max(cands, key=lambda t: len(t.get_text(strip=True)), default=None)
    return best or soup.body or soup


def parse_html(html: str) -> tuple[str, list[dict]]:
    soup = BeautifulSoup(html, "lxml")
    title = re.sub(r"\s+", " ", soup.title.get_text(strip=True)) if soup.title else ""
    out: list[str] = []
    _walk(_container(soup), out, [])
    # drop exact consecutive duplicates (devsite repeats summaries) and empty bullets
    lines, prev = [], None
    for l in out:
        if l == prev or l in ("-", "- "):
            continue
        lines.append(l)
        prev = l
    text = "\n\n".join(lines)
    sections, cur, buf = [], title or "intro", []
    for block in text.split("\n\n"):
        m = _HEAD.match(block.strip().split("\n")[0]) if block.strip() else None
        if m and "\n" not in block.strip():
            if "".join(buf).strip():
                sections.append({"section": cur, "text": "\n\n".join(buf).strip()})
            cur, buf = m.group(2).strip(), []
        else:
            buf.append(block)
    if "".join(buf).strip():
        sections.append({"section": cur, "text": "\n\n".join(buf).strip()})
    return title, sections
