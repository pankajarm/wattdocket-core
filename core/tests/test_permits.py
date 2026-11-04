"""The permit reader's one job: never publish a citation it did not verify."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from wattdocket.permits import (          # noqa: E402
    PermitDocument,
    QuoteNotFound,
    _verified_from_proposals,
    extract_claims,
    facility_identity,
    read_with_model,
)

PDF = pathlib.Path(__file__).parent.parent.parent / "data" / "permits" / "tceq_amazon_ale60_pbr164110.pdf"

pytestmark = pytest.mark.skipif(not PDF.exists(), reason="permit fixture not present")


@pytest.fixture(scope="module")
def doc() -> PermitDocument:
    return PermitDocument.load(PDF)


def test_document_hash_is_over_the_bytes_on_disk(doc: PermitDocument):
    import hashlib
    assert doc.sha256 == hashlib.sha256(PDF.read_bytes()).hexdigest()
    assert doc.page_count == 2


def test_page_number_is_derived_from_the_text_not_supplied(doc: PermitDocument):
    """Same quote, two pages, two different answers — because they are facts.

    If the page were a stored field this test could not exist.
    """
    assert doc.find("maximum operational schedule of 2,055 hours per year")[0] == 1
    assert doc.find("maximum annual operating hours shall not exceed 10%")[0] == 2


def test_line_number_points_at_a_real_line(doc: PermitDocument):
    """The cited line must contain the start of the quote on the real page."""
    page, line = doc.find("Bexar County is classified as serious nonattainment")
    raw_line = doc.raw_pages[page - 1].split("\n")[line - 1]
    assert "Bexar County is classified as serious nonattainment" in raw_line


def test_a_quote_that_is_not_present_is_refused(doc: PermitDocument):
    with pytest.raises(QuoteNotFound):
        doc.find("emissions shall be limited to three tons per year")


def test_a_paraphrase_is_refused(doc: PermitDocument):
    """The model's most common failure is a tidy paraphrase, not a fabrication."""
    with pytest.raises(QuoteNotFound):
        doc.find("the facility may run generators for up to 2,055 hours annually")


def test_short_quotes_cannot_be_verified(doc: PermitDocument):
    with pytest.raises(QuoteNotFound):
        doc.find("diesel")


def test_deterministic_extraction_cites_the_real_facts(doc: PermitDocument):
    claims = {c["metric"]: c for c in extract_claims(doc)}
    assert claims["generator_hours_cap"]["value"] == 2055.0
    assert claims["diesel_storage_gallons"]["value"] == 80000.0
    assert "serious nonattainment" in claims["ozone_nonattainment"]["detail"]
    for c in claims.values():
        assert c["citation"]["verified"] is True
        assert c["citation"]["page"] in (1, 2)


def test_facility_identity_comes_from_the_document(doc: PermitDocument):
    ident = facility_identity(doc)
    assert ident["company"].startswith("Amazon Data Services")
    assert ident["county"] == "Bexar"
    assert "Potranco" in ident["address"]


# ------------------------------------------------------------------ the gate
def test_unverifiable_model_quotes_are_dropped_not_downgraded(doc: PermitDocument):
    proposals = [
        {"metric": "real", "value": 2055, "unit": "hours/year",
         "quote": "maximum operational schedule of 2,055 hours per year"},
        {"metric": "invented", "value": 500, "unit": "hours/year",
         "quote": "generators may operate for 500 hours per year"},
        {"metric": "mostly_right", "value": 10, "unit": "%",
         "quote": "maximum annual operating hours shall not exceed ten percent"},
    ]
    good, bad = _verified_from_proposals(doc, proposals)
    assert [c["metric"] for c in good] == ["real"]
    assert {b["metric"] for b in bad} == {"invented", "mostly_right"}


def test_model_reader_reports_its_rejections(doc: PermitDocument):
    """A run that verified nothing must be visibly a failure, not an empty list."""

    def fake_model(_prompt: str) -> str:
        return ('[{"metric":"x","value":1,"unit":"t/yr",'
                '"quote":"this sentence does not appear in the permit"},'
                '{"metric":"y","value":2055,"unit":"hours/year",'
                '"quote":"maximum operational schedule of 2,055 hours per year"}]')

    result = read_with_model(doc, fake_model)
    assert result["proposed"] == 2
    assert len(result["verified"]) == 1
    assert len(result["rejected"]) == 1
    assert result["verified"][0]["citation"]["page"] == 1


def test_model_reader_survives_prose_instead_of_json(doc: PermitDocument):
    def chatty_model(_prompt: str) -> str:
        return "I could not find any obligations worth citing."

    result = read_with_model(doc, chatty_model)
    assert result["proposed"] == 0
    assert result["verified"] == []


# --------------------------------------------------------------- conflicts
def test_the_permit_contradicts_itself_and_we_report_both(doc: PermitDocument):
    """Real finding, first permit read: 2,055 vs 2,025 hours/year, no explanation.

    The product must not resolve this by picking the more convenient number.
    """
    from wattdocket.permits import detect_value_conflicts

    conflicts = detect_value_conflicts(doc)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["metric"] == "annual_generator_hours"
    assert c["values"] == [2025.0, 2055.0]
    assert c["spread"] == 30.0
    assert {o["page"] for o in c["occurrences"]} == {1, 2}
