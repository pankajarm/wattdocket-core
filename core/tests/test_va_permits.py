"""Virginia permits: page from the document's own footer, provenance stated.

The cache in data/va_permits/ means these run offline. If the cache is absent the
tests skip rather than reach the network — a test suite must not depend on a
third-party extraction service being up.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from wattdocket.va_permits import (  # noqa: E402
    PermitDocument,
    QuoteNotFound,
    extract_obligations,
    split_pages,
)

ROOT = pathlib.Path(__file__).parent.parent.parent
CACHE = ROOT / "data" / "va_permits" / "30142_3.txt"
pytestmark = pytest.mark.skipif(not CACHE.exists(), reason="permit text not cached")

URL = "https://www.deq.virginia.gov/home/showpublisheddocument/25646/638754663915730000"


@pytest.fixture(scope="module")
def doc() -> PermitDocument:
    return PermitDocument.from_text(CACHE.read_text(), URL)


def test_pages_are_recovered_from_the_documents_own_footers(doc: PermitDocument):
    """Virginia prints "… February 12, 2025 Page 8" at each page foot."""
    assert len(doc.pages) >= 10
    assert doc.page_count_claimed in (None, len(doc.pages), len(doc.pages) + 2)
    assert all(isinstance(k, int) for k in doc.pages)


def test_a_known_obligation_lands_on_the_page_that_prints_it(doc: PermitDocument):
    page, _ = doc.find("shall not operate more than 100 hours per year")
    assert page == 8


def test_the_fuel_cap_lands_on_page_nine(doc: PermitDocument):
    page, _ = doc.find("235,200 gallons")
    assert page == 9


def test_a_paraphrase_is_refused(doc: PermitDocument):
    with pytest.raises(QuoteNotFound):
        doc.find("generators may run for a hundred hours each year")


def test_citations_carry_their_retrieval_route(doc: PermitDocument):
    """The text did not come from a PDF we hashed, and the citation must say so."""
    cit = doc.cite("shall consume no more than 235,200 gallons")
    assert cit["retrieved_via"].startswith("https://r.jina.ai/")
    assert cit["verbatim_verified"] is True
    assert cit["page_derivation"] == "the document's own printed page footer"
    assert cit["page"] == 9


def test_extraction_finds_the_three_operating_caps(doc: PermitDocument):
    obls = {o["metric"]: o for o in extract_obligations(doc)}
    assert obls["generator_hours_limit"]["value"] == 100
    assert obls["fuel_throughput_limit"]["value"] == 235200
    assert obls["fuel_sulfur_limit"]["value"] == 0.0015
    for o in obls.values():
        # every one resolves to a page inside the document
        assert 1 <= o["citation"]["page"] <= 20


def test_quotes_are_complete_conditions_not_fragments(doc: PermitDocument):
    """The first version cut on 'Ref.' and produced '… (Ref.'"""
    for o in extract_obligations(doc):
        q = o["citation"]["quote"]
        assert not q.endswith("(Ref.")
        assert len(q) > 40
        assert q[0].isdigit() or q[0].isupper(), q[:40]


def test_page_split_ignores_a_document_without_footers():
    pages = split_pages("Title: x\nMarkdown Content:\nno footers here at all")
    assert pages == {1: "no footers here at all"}


# --------------------------------------------------- value / citation coupling
def test_every_value_appears_in_the_sentence_it_cites():
    """The bug this guards: a card whose number its own receipt contradicts.

    The first implementation searched the whole document for the value, then
    searched page by page for a citation, and the two could land on *different*
    matches — Equinix DC-21/DC-22 reported 11 hours with a quote saying 19.
    """
    import json

    facts = ROOT / "artifacts" / "va_permit_facts.json"
    if not facts.exists():
        pytest.skip("no va_permit_facts.json")
    data = json.loads(facts.read_text())
    bad = []
    total = 0
    for reg, rec in data.items():
        for o in rec.get("obligations") or []:
            total += 1
            quote = (o.get("citation") or {}).get("quote", "")
            flat = quote.replace(",", "")
            value = float(o["value"])
            as_text = f"{value:g}"
            as_int = f"{value:.0f}"
            if as_text not in flat and as_int not in flat:
                bad.append((reg, o["metric"], value, quote[:60]))
    assert total > 200, total
    assert not bad, bad[:5]


def test_mcrt_and_total_hours_are_separate_metrics():
    """Virginia caps maintenance/testing separately from emergency operation.

    Collapsing them would understate what a site may do: 17 hours of scheduled
    testing plus a separate 500-hour total is not a 17-hour site.
    """
    import json

    facts = ROOT / "artifacts" / "va_permit_facts.json"
    if not facts.exists():
        pytest.skip("no va_permit_facts.json")
    data = json.loads(facts.read_text())
    both = [
        reg for reg, rec in data.items()
        if {o["metric"] for o in rec.get("obligations") or []}
        >= {"mcrt_hours_limit", "generator_hours_limit"}
    ]
    assert len(both) > 20, len(both)
