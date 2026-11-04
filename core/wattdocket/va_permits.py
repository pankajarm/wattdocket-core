"""Read Virginia DEQ air permits, and be honest about how they were obtained.

Two problems make this different from the Texas reader.

**1. The origin blocks this machine.** `deq.virginia.gov` serves the permit PDFs
through an Akamai rule that returns 403 to a plain HTTP client *and* to a real
browser from this host, while the document itself is public and linked from the
Commonwealth's own registry page. So the text is retrieved through a text
extraction service. That is a provenance fact, not a detail: the bytes we quote
did not come from a PDF this process hashed. Every citation therefore carries
`retrieved_via` and the card must show it.

**2. There is no PDF here to derive a page from.** The Texas reader derives a
page number by searching text it extracted itself. Here the page number comes
from the document's *own printed footer* — Virginia writes
"…Registration Number: 30142 February 12, 2025 Page 8" at the foot of each page.
Splitting on that marker recovers the pagination the Commonwealth itself asserts,
which is stronger evidence than an offset into a re-flowed text layer.

    from wattdocket.va_permits import fetch_permit
    doc = fetch_permit("https://www.deq.virginia.gov/home/showpublisheddocument/25646/…")
    doc.cite("shall not operate more than 100 hours per year")
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

PROXY = "https://r.jina.ai/"
_WS = re.compile(r"\s+")
# The footer Virginia prints: "... February 12, 2025 Page 8"
_FOOTER = re.compile(r"(?:Page|page)\s+(\d{1,3})\s*(?=\n|$|[A-Z])")


class QuoteNotFound(Exception):
    pass


def _norm(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def fetch_text(url: str, timeout: int = 90) -> str:
    """Retrieve a permit's text through the extraction service."""
    target = url if url.startswith(PROXY) else PROXY + url
    req = urllib.request.Request(target, headers={"Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def split_pages(raw: str) -> dict[int, str]:
    """Split on the document's own page footers.

    Content *after* a "Page N" footer belongs to page N+1, because the footer
    closes the page it appears on. Page 1 has no footer before it, so the text
    ahead of the first marker is page 1.
    """
    body = raw.split("Markdown Content:", 1)[-1]
    marks = list(_FOOTER.finditer(body))
    pages: dict[int, str] = {}
    if not marks:
        return {1: _norm(body)}

    pages[1] = _norm(body[: marks[0].start()])
    for i, m in enumerate(marks[:-1]):
        n = int(m.group(1))
        pages[n + 1] = _norm(body[m.end(): marks[i + 1].start()])
    last = marks[-1]
    tail = _norm(body[last.end():])
    if tail:
        pages[int(last.group(1)) + 1] = tail
    return {k: v for k, v in pages.items() if v}


@dataclass
class PermitDocument:
    url: str
    title: str
    page_count_claimed: int | None
    pages: dict[int, str] = field(default_factory=dict)
    retrieved_via: str = PROXY
    registration: str | None = None

    @classmethod
    def from_text(cls, raw: str, url: str) -> "PermitDocument":
        title = ""
        claimed = None
        for line in raw.splitlines()[:8]:
            if line.startswith("Title:"):
                title = line.split(":", 1)[1].strip()
            elif line.startswith("Number of Pages:"):
                try:
                    claimed = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        pages = split_pages(raw)
        m = re.search(r"Registration No\.?:?\s*(\d{2,6})", raw)
        return cls(url=url, title=title, page_count_claimed=claimed,
                   pages=pages, registration=m.group(1) if m else None)

    def find(self, quote: str) -> tuple[int, str]:
        """(page number the document prints, matched page text)."""
        target = _norm(quote)
        if len(target) < 12:
            raise QuoteNotFound("quote too short to verify")
        for page_no in sorted(self.pages):
            if target in self.pages[page_no]:
                return page_no, self.pages[page_no]
        raise QuoteNotFound(f"not found verbatim: {target[:70]!r}")

    def cite(self, quote: str) -> dict:
        page, _text = self.find(quote)
        return {
            "document": self.title or self.url.rsplit("/", 2)[-2],
            "document_id": f"VA DEQ registration {self.registration}" if self.registration else self.url,
            "publisher": "Virginia Department of Environmental Quality",
            "page": page,
            "page_derivation": "the document's own printed page footer",
            "quote": _norm(quote),
            "url": self.url,
            # The honest caveat: the text did not come from a PDF we hashed.
            "retrieved_via": self.retrieved_via,
            "verbatim_verified": True,
        }


def fetch_permit(url: str) -> PermitDocument:
    return PermitDocument.from_text(fetch_text(url), url)


# ------------------------------------------------------------------ obligations
# Each pattern is a condition Virginia writes in a fixed form. Narrow on purpose:
# a broad extractor produces plausible obligations, these produce checkable ones.
_OBLIGATIONS = [
    {
        "metric": "mcrt_hours_limit",
        "unit": "hours/year per generator",
        "pattern": (r"shall not operate more than\s*(?P<value>[\d,]+)\s*"
                    r"hours?\s*(?:per|/) ?year\s*for\s*scheduled maintenance"),
        "value_group": "value",
    },
    {
        # The overall cap, when the permit states one separately from MCRT.
        "metric": "generator_hours_limit",
        "unit": "hours/year per generator",
        "pattern": (r"shall not operate more than\s*(?P<value>[\d,]+)\s*"
                    r"hours?\s*(?:per|/) ?year(?!\s*for\s*scheduled maintenance)"),
        "value_group": "value",
    },
    {
        "metric": "fuel_throughput_limit",
        "unit": "gallons/year",
        "pattern": (r"shall consume no more than\s*(?P<value>[\d,]+)\s*gallons"),
        "value_group": "value",
    },
    {
        "metric": "fuel_sulfur_limit",
        "unit": "% sulfur by shipment",
        "pattern": r"maximum sulfur content per shipment of\s*(?P<value>[\d.]+)\s*%",
        "value_group": "value",
    },
]


_ABBREV = ("Ref", "Nos", "No", "Eq", "vs", "e.g", "i.e", "approx", "Sec", "Para",
           "Inc", "Ltd", "Co", "Reg", "Fig", "Art")


def _sentence_start(text: str, start: int, look_back: int = 400) -> int:
    """Index of the last real sentence boundary before `start`.

    "Ref. Nos." and "No." are abbreviations, not full stops. Treating them as
    boundaries produced quotes like "… diesel fire pump (Ref." — and, worse, a
    window that began at an *earlier* sentence containing a different number
    than the one being cited, so a reader saw "19 hours" at the top of a claim
    about 11.
    """
    left = max(0, start - look_back)
    boundary = None
    for m in re.finditer(r"\.\s+(?=[A-Z(\d])", text[left:start]):
        at = left + m.start()
        before = text[max(0, at - 10):at].rstrip()
        if any(before.endswith(a) for a in _ABBREV):
            continue
        boundary = left + m.end()          # just after the whitespace
    if boundary is not None:
        return boundary

    # No boundary found: at least start on a word.
    idx = left
    while idx < start and idx > 0 and text[idx - 1].isalnum() and text[idx].isalnum():
        idx += 1
    return idx


def _condition_window(text: str, start: int, end: int) -> str:
    """The sentence containing the obligation, complete and citable to one page."""
    left = _sentence_start(text, start)
    stop = len(text)
    for m_stop in re.finditer(r"\.\s+[A-Z(]", text[end:end + 220]):
        at = end + m_stop.start()
        before = text[max(0, at - 10):at].rstrip()
        if any(before.endswith(a) for a in _ABBREV):
            continue
        stop = at + 1
        break
    return text[left:stop].strip()


def extract_obligations(doc: PermitDocument) -> list[dict]:
    """One value per metric, and the value comes from the sentence we cite.

    A previous version searched the whole document for the value, then searched
    page by page for a citation — and the two could find *different* matches. On
    Equinix DC-21/DC-22 it reported 11 hours with a quote saying 19. The card
    would have shown a number its own receipt contradicted, which is the exact
    failure this product exists to catch.

    So the search runs page by page and value and quote come from one match.
    Anything that does not resolve inside a single page is dropped.
    """
    out = []
    seen: set[str] = set()
    for spec in _OBLIGATIONS:
        for page_no in sorted(doc.pages):
            hit = re.search(spec["pattern"], doc.pages[page_no], re.IGNORECASE)
            if not hit:
                continue
            raw = hit.group(spec["value_group"]).replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            quote = _condition_window(doc.pages[page_no], hit.start(), hit.end())
            try:
                cit = doc.cite(quote)
            except QuoteNotFound:
                # The window drifted off the page; fall back to the match itself.
                try:
                    cit = doc.cite(hit.group(0))
                    quote = hit.group(0)
                except QuoteNotFound:
                    continue
            if spec["metric"] in seen:
                continue
            seen.add(spec["metric"])
            out.append({
                "metric": spec["metric"],
                "value": value,
                "unit": spec["unit"],
                "citation": cit,
            })
    return out


# --------------------------------------------------------------- equipment table
# Virginia prints an equipment table: "(12) Caterpillar Model 3516C-HD emergency
# diesel engine gen-sets  3,633 bhp  2,500 ekW". Reading it gives installed
# nameplate capacity, which is what turns a permitted hours cap into a permitted
# energy envelope. It is a table, so the parse is positional and narrow — no
# general extraction, and a row that does not resolve is skipped rather than
# guessed at.
_EQUIP_ROW = re.compile(
    r"\((\d{1,3})\)\s*[^\n]{0,120}?gen-?sets?[^\n]{0,160}?"
    r"(?P<ekw>[\d,]{3,6})\s*(?:e?kW|kilowatts?)\b",
    re.IGNORECASE | re.DOTALL)


def extract_equipment(doc: PermitDocument) -> list[dict]:
    """Installed generator capacity from the equipment table, with a citation.

    Returns at most one aggregate row per document. Two numbers matter: how many
    units, and the nameplate rating of each. Rows are summed only when both
    appear in the same sentence of the table.
    """
    for page_no in sorted(doc.pages):
        text = doc.pages[page_no]
        rows = list(_EQUIP_ROW.finditer(text))
        if not rows:
            continue
        units = 0
        kw_total = 0.0
        ratings: list[float] = []
        for m in rows:
            try:
                n = int(m.group(1))
                kw = float(m.group("ekw").replace(",", ""))
            except ValueError:
                continue
            if not (1 <= n <= 500 and 50 <= kw <= 20000):
                continue
            units += n
            kw_total += n * kw
            ratings.append(kw)
        if units >= 2:
            first = rows[0]
            quote = text[max(0, first.start() - 40):first.end() + 10].strip()
            try:
                cit = doc.cite(quote)
            except QuoteNotFound:
                cit = None
            return [{
                "metric": "generator_units",
                "value": units,
                "unit": "generators (equipment table)",
                "rated_ekw": round(kw_total, 0),
                "mw_total": round(kw_total / 1000.0, 1),
                "ratings_ekw": sorted(set(ratings)),
                "rows": len(rows),
                "citation": cit,
            }]
    return []


if __name__ == "__main__":
    import sys

    url = sys.argv[1]
    doc = fetch_permit(url)
    print(f"title: {doc.title}")
    print(f"registration: {doc.registration}")
    print(f"pages claimed: {doc.page_count_claimed} | pages recovered: {len(doc.pages)}")
    print(f"retrieved via: {doc.retrieved_via}")
    obls = extract_obligations(doc)
    print(f"obligations: {len(obls)}")
    for o in obls:
        c = o["citation"]
        print(f"  {o['metric']:24s} {o['value']:>12,.2f} {o['unit']:28s} p{c['page']}")
        print(f"      “{c['quote'][:110]}”")
    print(json.dumps([{k: v for k, v in o.items() if k != "citation"} for o in obls], indent=1))
