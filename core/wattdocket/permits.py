"""Read an air permit and cite it by page — with the page derived, not trusted.

The failure mode this module exists to prevent: a language model reads a
200-page permit, returns the right sentence, and the wrong page number. The
sentence is verifiable by eye; the page number isn't, so a reader who checks it
finds nothing there and stops believing the rest of the card.

So the model is never asked where the quote lives. It is asked for the quote
alone. Python then finds that exact string in the page texts it extracted
itself, and the page number is whatever page the search landed on. A quote that
cannot be found is not downgraded to "model says page 41" — it is refused, and
the claim does not ship.

    doc = PermitDocument.load("data/permits/tceq_amazon_ale60_pbr164110.pdf")
    c = doc.cite("maximum operational schedule of 2,055 hours per year")
    c["page"], c["line"]        # both derived from the text search
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# Text extracted from PDFs arrives with line breaks in the middle of sentences
# and runs of spaces from justified layout. Normalising both sides is what makes
# a verbatim check possible without pretending the PDF is clean text.
_WS = re.compile(r"\s+")


class QuoteNotFound(Exception):
    """The quote is not in the document. The claim must not be published."""


def _norm(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def _normalise_with_map(raw: str) -> tuple[str, list[int]]:
    """Collapse whitespace, keeping a normalized-index -> raw-index map.

    Without the map there is no honest line number: the text searched is not the
    text on the page. With it, a position in the normalized string resolves to a
    real offset in the original, and the line number is counted from there.
    """
    out: list[str] = []
    index: list[int] = []
    in_ws = False
    for i, ch in enumerate(raw):
        if ch.isspace():
            if not in_ws and out:          # collapse a run to one space
                out.append(" ")
                index.append(i)
            in_ws = True
            continue
        in_ws = False
        out.append(ch)
        index.append(i)
    return "".join(out).strip(), index


@dataclass
class PermitDocument:
    path: Path
    sha256: str
    pages: list[str] = field(default_factory=list)
    raw_pages: list[str] = field(default_factory=list)
    maps: list[list[int]] = field(default_factory=list)
    title: str = ""

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | Path) -> "PermitDocument":
        path = Path(path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()

        try:
            import pymupdf
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "pymupdf is required to read permits: pip install pymupdf") from exc

        with pymupdf.open(path) as pdf:
            raw_pages = [pdf[i].get_text() for i in range(len(pdf))]
            title = (pdf.metadata or {}).get("title") or path.stem

        pages, maps = [], []
        for raw in raw_pages:
            text, index = _normalise_with_map(raw)
            # strip() can drop leading characters, so rebuild the map to match
            lead = len(raw) - len(raw.lstrip())
            pages.append(text)
            maps.append(index[lead:lead + len(text)])

        return cls(path=path, sha256=digest, pages=pages,
                   raw_pages=raw_pages, maps=maps, title=title)

    # ---------------------------------------------------------------- citing
    @property
    def page_count(self) -> int:
        return len(self.pages)

    def find(self, quote: str) -> tuple[int, int]:
        """Locate a quote. Returns (page_number_1_based, line_number_1_based).

        The line number is derived the same way as the page: the quote's position
        within the page's own line structure, not the model's guess.
        """
        target = _norm(quote)
        if len(target) < 8:
            raise QuoteNotFound("quote is too short to verify meaningfully")

        # A quote spanning a page break cannot be verified against one page, and
        # inventing a page for it is exactly the dishonesty this module forbids.
        for page_no, text in enumerate(self.pages, start=1):
            idx = text.find(target)
            if idx < 0:
                continue
            # Resolve the normalized offset back to a real offset on the page,
            # then count newlines before it. The line number is a property of
            # the document, not of the search string.
            mapping = self.maps[page_no - 1]
            raw_idx = mapping[idx] if idx < len(mapping) else 0
            raw = self.raw_pages[page_no - 1]
            line = raw.count("\n", 0, raw_idx) + 1
            return page_no, line
        raise QuoteNotFound(f"not found verbatim: {target[:70]!r}")

    def cite(self, quote: str, *, label: str = "") -> dict:
        page, line = self.find(quote)
        return {
            "document_sha256": self.sha256,
            "document": self.path.name,
            "publisher": "Texas Commission on Environmental Quality",
            "page": page,
            "line": line,
            "quote": _norm(quote),
            "label": label,
            # Derived here, by this module, from the bytes on disk.
            "page_derivation": "text search over extracted page text",
            "verified": True,
        }

    def cite_many(self, quotes: list[str]) -> list[dict]:
        return [self.cite(q) for q in quotes]


# -------------------------------------------------------------------- claims
# Deliberately narrow: each pattern is a fact the permit states in a fixed form,
# and each one is something a resident or an operator would want pinned. Broad
# extraction would produce plausible claims; these produce checkable ones.
_HOURS = re.compile(
    r"(?:maximum operational schedule of|shall not exceed|limited to)\s*"
    r"(?P<hours>[\d,]+)\s*(?:hours|hr)\s*(?:per|/)\s*year", re.IGNORECASE)
_TANKS = re.compile(
    r"(?P<count>\w+)\s*\((?P<num>\d+)\)\s*new\s*(?P<gallons>[\d,]+)-gallon\s*"
    r"capacity diesel fuel storage tanks?", re.IGNORECASE)
_NONATTAIN = re.compile(
    r"(?P<county>[A-Z][a-z]+(?: [A-Z][a-z]+)?) County is classified as "
    r"(?P<severity>\w+) nonattainment for the (?P<standard>[0-9]{4} 8-hour ozone)",
    re.IGNORECASE)
_GEN_UNITS = re.compile(r"EPNs?\s*(?P<units>EG[\d\-–A-Za-z, and]+)", re.IGNORECASE)


def extract_claims(doc: PermitDocument) -> list[dict]:
    """Facts stated verbatim in the permit, each with a derived citation."""
    claims: list[dict] = []

    page1 = doc.pages[0] if doc.pages else ""
    full = " ".join(doc.pages)

    m = _HOURS.search(full)
    if m:
        quote = m.group(0)
        try:
            c = doc.cite(quote)
            claims.append({
                "metric": "generator_hours_cap",
                "value": float(m.group("hours").replace(",", "")),
                "unit": "hours/year",
                "quote": c["quote"],
                "citation": c,
            })
        except QuoteNotFound:
            pass

    m = _NONATTAIN.search(full)
    if m:
        try:
            c = doc.cite(m.group(0))
            claims.append({
                "metric": "ozone_nonattainment",
                "value": None,
                "unit": "",
                "detail": f"{m.group('county')} County — {m.group('severity')} "
                          f"nonattainment, {m.group('standard')}",
                "citation": c,
            })
        except QuoteNotFound:
            pass

    m = _TANKS.search(full)
    if m:
        try:
            c = doc.cite(m.group(0))
            claims.append({
                "metric": "diesel_storage_gallons",
                "value": float(m.group("num")) * float(m.group("gallons").replace(",", "")),
                "unit": "gallons",
                "detail": f"{m.group('num')} tanks x {m.group('gallons')} gal",
                "citation": c,
            })
        except QuoteNotFound:
            pass

    return claims


_HOURS_ANY = re.compile(r"(?P<hours>\d{1,2},?\d{3})\s*(?:hours|hrs)(?:\s*/\s*|\s+per\s+)(?:year|yr)",
                        re.IGNORECASE)


def detect_value_conflicts(doc: PermitDocument) -> list[dict]:
    """Different numbers for the same limit, inside one document.

    Found on the first real permit we read: the description authorises "2,055
    hours per year across all generators", while the emissions-basis note on the
    application says "a maximum of 2,025 hours per year". Thirty hours apart, in
    a two-page document, with no explanation.

    A summariser would pick one and sound confident. This function refuses to
    pick: it reports both with their citations so a human can see that the state
    has written down two different numbers. That is a more useful answer than
    either number alone, and it is only possible because every value keeps its
    own receipt.
    """
    found: dict[str, list[dict]] = {}
    for page_no, text in enumerate(doc.pages, start=1):
        for m in _HOURS_ANY.finditer(text):
            value = float(m.group("hours").replace(",", ""))
            if value < 10:                     # 10% style, not an hour count
                continue
            quote = text[max(0, m.start() - 90):m.end() + 30].strip()
            key = "annual_generator_hours"
            found.setdefault(key, []).append({
                "value": value,
                "unit": "hours/year",
                "page": page_no,
                "context": quote,
            })

    conflicts = []
    for key, items in found.items():
        values = sorted({i["value"] for i in items})
        if len(values) > 1:
            conflicts.append({
                "metric": key,
                "values": values,
                "spread": max(values) - min(values),
                "occurrences": items,
            })
    return conflicts


# ------------------------------------------------------- equipment specifics
# What the permit says is physically on the site. These are the numbers the
# public card currently answers with "not published", so finding them here is
# the point of the permit lane.
_GEN_COUNT = re.compile(
    r"(?P<count>\d{1,4})\s+(?:diesel\s+)?(?:emergency\s+)?generators?\b",
    re.IGNORECASE)
_GEN_RATING = re.compile(
    r"(?:power rating is|greater than|rated at)\s*(?P<kw>[\d,]{3,7})\s*(?P<unit>kW|KW|kilowatts?)",
    re.IGNORECASE)


def _sentence_window(text: str, start: int, end: int, pad: int = 45) -> str:
    """A quote that starts at a sentence boundary rather than mid-phrase.

    The first version took a fixed 90-character window, so a drawer opened on
    "...of Determination Permit Area Process Description At full build-out, the
    facility features 86 diesel emergency generators" — verifiable, but it reads
    like a photocopy artefact. Starting at the preceding full stop costs nothing
    and is what a reader expects.
    """
    left = max(0, start - pad)
    # Never start a quote mid-word: pad lands inside "Determination" otherwise,
    # which produced quotes beginning "ion At full build-out…".
    if left > 0 and text[left - 1].isalnum() and text[left].isalnum():
        nxt = text.find(" ", left, start)
        if nxt != -1:
            left = nxt + 1
    boundary = text.rfind(". ", left, start)
    if boundary != -1:
        left = boundary + 2
    quote = text[left:end + 25].strip()
    # Page furniture ("Page 2 of 14 Operating Permit Basis of Determination
    # Permit Area Process Description …") carries no meaning for a reader and
    # makes the drawer read like a photocopy artefact. Cutting at a known
    # heading is safer than a sentence-boundary guess: an earlier attempt with a
    # lookahead regex chopped "Determination" into "ion".
    quote = re.sub(r"^Page \d+ of \d+\s+", "", quote)
    for heading in ("Process Description", "Applicability Determinations",
                    "Basis of Determination", "Permit Area",
                    "Emissions Limitations and Standards"):
        idx = quote.find(heading)
        if 0 <= idx <= 80:
            quote = quote[idx + len(heading):].strip(" :=-")
            break
    return quote


def extract_equipment(doc: PermitDocument) -> list[dict]:
    """Generator count and unit rating, each cited to the page it came from.

    Deliberately conservative: a count is only accepted when the sentence also
    says what kind of machine it is. "86 diesel emergency generators" is a fact;
    a bare "86 units" is not, and is skipped.
    """
    out: list[dict] = []

    # Search page by page, and quote a window of *that page's* normalised text.
    # Joining the pages first produced a quote that spanned a page boundary, so
    # the verbatim check (correctly) refused it and the count lost its citation.
    for page_no, text in enumerate(doc.pages, start=1):
        m = _GEN_COUNT.search(text)
        if not m:
            continue
        count = int(m.group("count").replace(",", ""))
        if not 2 <= count <= 2000:          # a stray number, not a fleet
            continue
        window = _sentence_window(text, m.start(), m.end())
        try:
            cit = doc.cite(window.strip())
        except QuoteNotFound:
            cit = None
        out.append({
            "metric": "backup_generator_count",
            "value": count,
            "unit": "generators",
            "citation": cit,
        })
        break

    for page_no, text in enumerate(doc.pages, start=1):
        m = _GEN_RATING.search(text)
        if not m:
            continue
        kw = float(m.group("kw").replace(",", ""))
        if not 50 <= kw <= 20000:
            continue
        window = _sentence_window(text, m.start(), m.end(), pad=30)
        try:
            cit = doc.cite(window.strip())
        except QuoteNotFound:
            cit = None
        out.append({
            "metric": "generator_unit_rating_kw",
            "value": kw,
            "unit": "kW per unit (lower bound — the permit says 'greater than')",
            "citation": cit,
        })
        break

    return out


def facility_identity(doc: PermitDocument) -> dict:
    """Whatever the document says about who and where — not inferred."""
    text = doc.pages[0] if doc.pages else ""
    out: dict[str, str] = {}
    patterns = {
        "company": r"Company:\s*(.+?)\s*(?:Registration No|Nearest City|Physical Location|$)",
        "permit_no": r"Permit Number:?\s*([A-Z]{0,2}\d{3,6})",
        "registration": r"(?:Registration|Permit) No\.?:?\s*([\w\-]+)",
        "county": r"County:\s*([A-Z][A-Za-z ]*?)\s*(?:Project Type|Project Reviewer|Permit Number|NAICS|$)",
        "city": r"Nearest City:\s*(.+?)\s*(?:Project No|County|Physical Location)",
        # Stop at the next label, not at end-of-page: TCEQ writes
        # "Physical Location: 14720 Omicron Dr Nearest City: San Antonio
        # County: Bexar" on one line, and a greedy match swallowed the lot.
        "address": r"Physical Location:\s*(.+?)\s*(?:Nearest City|County:|Project Overview|$)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            out[key] = _norm(m.group(1))[:120]
    return out


# ------------------------------------------------------------- model reader
# The model is used for the thing it is good at: reading prose and deciding what
# matters. It is never asked where in the document a sentence lives, and its
# output is treated as a proposal until this module has found the quote itself.
READ_PROMPT = """You are reading an air permit. Extract at most six obligations that
a resident or an operator would want pinned to a citation.

Return ONLY a JSON array. Each item: {{"metric": snake_case_name,
"value": number or null, "unit": short_unit, "quote": EXACT-SUBSTRING}}.

Rules:
- "quote" must be copied character-for-character from the text, 15-300 characters.
- Do not paraphrase, shorten with ellipses, or merge two sentences.
- Do not invent a page number; the caller derives it.
- If the document states no such obligation, return [].

TEXT:
{text}
"""


def _verified_from_proposals(doc: PermitDocument, proposals: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split model proposals into verified claims and rejected ones.

    A rejection is not an error to swallow: it is the evidence that the gate
    works, and it is reported so a reader can see how many survived.
    """
    good, bad = [], []
    for item in proposals:
        quote = str(item.get("quote") or "")
        try:
            citation = doc.cite(quote)
        except QuoteNotFound as exc:
            bad.append({"metric": item.get("metric"), "reason": str(exc),
                        "quote": quote[:90]})
            continue
        good.append({
            "metric": str(item.get("metric") or "unnamed"),
            "value": item.get("value"),
            "unit": str(item.get("unit") or ""),
            "citation": citation,
        })
    return good, bad


def read_with_model(doc: PermitDocument, chat_fn, max_pages: int = 12) -> dict:
    """Ask a model what the permit obliges, then verify every quote.

    `chat_fn(prompt) -> str` is injected so the gate is testable without a
    network call, and so the same code path runs against a fixture.
    """
    import json as _json
    import re as _re

    # Send the pages that actually carry obligations. A 214-page application
    # packet is mostly forms; the first pages hold the operative text.
    body = "\n\n".join(doc.pages[:max_pages])
    raw = chat_fn(READ_PROMPT.format(text=body[:60000]))

    match = _re.search(r"\[.*\]", raw, _re.DOTALL)
    try:
        proposals = _json.loads(match.group(0)) if match else []
    except _json.JSONDecodeError:
        proposals = []

    good, bad = _verified_from_proposals(doc, proposals)
    return {
        "document": doc.path.name,
        "document_sha256": doc.sha256,
        "proposed": len(proposals),
        "verified": good,
        "rejected": bad,
    }


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    args = ap.parse_args()

    document = PermitDocument.load(args.pdf)
    print(f"sha256: {document.sha256}")
    print(f"pages:  {document.page_count}")
    print(f"who:    {json.dumps(facility_identity(document), indent=2)}")
    claims = extract_claims(document)
    print(f"claims: {len(claims)}")
    for c in claims:
        print(f"  p{c['citation']['page']} L{c['citation']['line']}  "
              f"{c['metric']:22s} {c.get('value')} {c['unit']}".rstrip())
        print(f"      “{c['citation']['quote'][:88]}”")
