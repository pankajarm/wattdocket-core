"""Claims, citations and the render gate — INV-7 and INV-8.

INV-7: a claim renders only if it carries a verified citation, or is a
MODELED claim whose calculation id resolves. This is enforced in code, not
in a code review: `Claim.renderable()` is the only door to the UI, and the
renderer takes the result of that method.

INV-8: an empty lookup is "not reported", never zero. A missing GHGRP row
means the facility sat under the reporting threshold — it does not mean the
facility emitted nothing. Confusing the two is the single most damaging
error this product could make.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


class Badge(str, Enum):
    MEASURED = "MEASURED"
    REPORTED = "REPORTED"
    MODELED = "MODELED"
    ESTIMATED = "ESTIMATED"


class CitationState(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNCHECKED = "UNCHECKED"


class NotPublished(str, Enum):
    """The first-class 'we do not have this' value. Never zero, never blank."""

    NOT_REPORTED = "NOT_REPORTED"
    NOT_PUBLISHED = "NOT_PUBLISHED"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"


def _normalise_text(s: str) -> str:
    """Normalise for substring comparison without changing meaning.

    Collapses whitespace and unifies dashes/quotes, which PDF extraction
    routinely mangles. Case is preserved as lowercase; no stemming, no fuzzy
    matching — a quote either appears on the page or it does not.
    """
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2014", "-").replace("\u2013", "-")
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


@dataclass(frozen=True)
class Citation:
    doc_sha256: str
    page: int
    line: int
    quote: str
    publisher: str = ""
    dataset: str = ""
    field: str = ""
    verified: bool = False
    verifier_version: str = "substring-1"

    def verify(self, page_text: str) -> "Citation":
        """Return a new Citation with `verified` set by substring match."""
        ok = _normalise_text(self.quote) in _normalise_text(page_text)
        return Citation(
            doc_sha256=self.doc_sha256, page=self.page, line=self.line,
            quote=self.quote, publisher=self.publisher, dataset=self.dataset,
            field=self.field, verified=ok, verifier_version=self.verifier_version,
        )


@dataclass(frozen=True)
class Claim:
    id: str
    metric: str
    value: float | NotPublished
    unit: str
    badge: Badge
    as_of_utc: str
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    provisional: bool = False
    citations: tuple[Citation, ...] = field(default_factory=tuple)
    calc_id: str | None = None

    def renderable(self) -> tuple[bool, str]:
        """The only gate to the UI. Returns (ok, reason_if_not)."""
        if isinstance(self.value, NotPublished):
            # "not published" is always renderable — it is an answer.
            return True, ""
        if self.badge in (Badge.MODELED, Badge.ESTIMATED):
            # Our own arithmetic is admissible only if it is reproducible:
            # a calc_id names the function and version that produced it.
            if not self.calc_id:
                return False, (
                    f"{self.badge.value} claim without calc_id (not reproducible)")
            return True, ""
        if not self.citations:
            return False, "no citation attached"
        if not any(c.verified for c in self.citations):
            return False, "no VERIFIED citation (quote not found on page)"
        return True, ""

    def as_dict(self) -> dict:
        ok, why = self.renderable()
        value = (self.value.value if isinstance(self.value, NotPublished)
                 else round(self.value, 3))
        return {
            "id": self.id,
            "metric": self.metric,
            "value": value,
            "unit": self.unit,
            "badge": self.badge.value,
            "provisional": self.provisional,
            "as_of": self.as_of_utc,
            "range": (None if self.p10 is None else
                      {"p10": round(self.p10, 1), "p50": round(self.p50, 1),
                       "p90": round(self.p90, 1)}),
            "drivers": [],
            "citations": [{
                "doc_sha256": c.doc_sha256, "page": c.page, "line": c.line,
                "quote": c.quote, "publisher": c.publisher,
                "dataset": c.dataset, "field": c.field,
                "verified": c.verified,
                "verifier_version": c.verifier_version,
            } for c in self.citations],
            "calc_id": self.calc_id,
            "sources": len(self.citations),
            "renderable": ok,
            "render_block_reason": why,
        }


def not_reported(metric: str, unit: str, as_of: str, why: NotPublished,
                 note: str = "") -> Claim:
    """Construct the deliberate absence of a number."""
    return Claim(
        id=f"{metric}-not-reported",
        metric=metric, value=why, unit=unit, badge=Badge.REPORTED,
        as_of_utc=as_of,
        citations=(Citation(doc_sha256="", page=0, line=0, quote=note,
                            verified=True),) if note else (),
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
