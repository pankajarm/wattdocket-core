import copy
from datetime import datetime, timezone
import hashlib
import io
import json

import pymupdf
import pytest

from wattdocket.production.fetch import CaptureFetcher
from wattdocket.production.permit_recovery import (
    REVIEW_VERSION, apply_recovery_review, native_pdf_pages, recovery_review_is_current,
)
from wattdocket.production.sources_facilities import extract_virginia_document
from wattdocket.production.storage import LocalStore

URL = "https://www.deq.virginia.gov/home/showpublisheddocument/123/456"


@pytest.fixture
def sample():
    p = pymupdf.open()
    p.new_page().insert_text((40, 40), "Registration No. 11790\nWise County. Site at 5978 Windswept Blvd.\nOriginal permit identity and reporting scope.")
    p.new_page().insert_text((40, 40), "The engine gen-sets EG01 through EG07 shall consume no more than\n583,500 gallons per year, calculated over consecutive 12-month periods.")
    raw = p.tobytes(); p.close()
    pages = native_pdf_pages(raw)
    corrected = pages[2].replace("583,500", "583,600")
    spec = {"registration": "11790-1", "publisher_url": URL,
            "document_sha256": hashlib.sha256(raw).hexdigest(), "page_count": 2,
            "reviewed_at": "2026-11-05T02:30:00Z", "document_extent": "full_permit",
            "identity_review": {"registration": {"value": "11790", "pdf_page": 1, "quote": "Registration No. 11790"}},
            "eligible_condition_pages": [2], "eligible_equipment_pages": [],
            "supersession_review": {"current_legal_effectiveness": "unknown", "statements": []},
            "corrections": [{"pdf_page": 2, "old_text": "583,500", "new_text": "583,600",
                             "visual_review_sha256": "a"*64, "reason": "Reviewed original image has600, not500"}],
            "conditions": [{"metric": "fuel_throughput_limit", "value": 583600, "unit": "gallons/year",
                            "pdf_page": 2, "quote": corrected, "subject_scope": "EG01 through EG07 combined",
                            "purpose": "fuel throughput", "period": "consecutive twelve months"}], "equipment": []}
    permit = {"registration": "11790-1", "permit_url": URL, "county": "Wise Co.", "county_fips": "195", "issued_iso": "2016-08-23"}
    doc = extract_virginia_document(raw, URL, retrieved_via=URL, registration="11790-1")
    return raw, doc, permit, {"11790-1": spec}


def test_transcription_preserves_original_pdf_hash_and_distinct_page_text_hashes(sample):
    raw, doc, permit, records = sample
    result = apply_recovery_review(doc, raw, permit, reviews=records)
    fact = result["obligations"][0];c = fact["citation"]
    assert fact["value"] == 583600 and "583,500" not in c["quote"]
    assert c["content_sha256"] == c["document_sha256"] == hashlib.sha256(raw).hexdigest()
    assert c["native_page_text_sha256"] != c["reviewed_page_text_sha256"]
    assert c["representation"] == "reviewed_pdf_transcription" and c["verbatim_verified"] is False
    assert result["extraction_version"] == REVIEW_VERSION
    assert recovery_review_is_current(result, permit, records)
    assert doc["obligations"][0]["value"] == 583500  # original extraction never overwritten


@pytest.mark.parametrize("mutation", ["page", "eligibility", "uncorrected_value", "invented_quote", "old_text", "registration", "current_effectiveness"])
def test_reviewed_numeric_and_identity_tampering_fails_closed(sample, mutation):
    raw, doc, permit, records = sample
    spec = records["11790-1"]
    if mutation == "page": spec["conditions"][0]["pdf_page"] = 1
    if mutation == "eligibility": spec["eligible_condition_pages"] = []
    if mutation == "uncorrected_value": spec["conditions"][0]["value"] = 583500
    if mutation == "invented_quote": spec["conditions"][0]["quote"] = "Unstated fuel limit583600"
    if mutation == "old_text": spec["corrections"][0]["old_text"] = "not in the page"
    if mutation == "registration": spec["identity_review"]["registration"]["value"] = "11791"
    if mutation == "current_effectiveness": spec["supersession_review"]["current_legal_effectiveness"] = "current"
    with pytest.raises(ValueError):
        apply_recovery_review(doc, raw, permit, reviews=records)


@pytest.mark.parametrize("change", ["pdf_bytes", "version_url"])
def test_real_document_version_change_withdraws_approved_numbers(sample, change):
    raw, doc, permit, records = sample
    if change == "pdf_bytes": raw += b"\n%changed original version"
    else: permit["permit_url"] += "7"
    result = apply_recovery_review(doc, raw, permit, reviews=records)
    assert result["recovery_review"]["status"] == "review_required"
    assert result["obligations"] == result["equipment"] == []
    assert not recovery_review_is_current(result, permit, records)


def test_reader_fallback_does_not_claim_original_pdf_changed(sample):
    _, doc, permit, records = sample
    with pytest.raises(ValueError, match="reader fallback does not establish"):
        apply_recovery_review(doc, b"Reader text of the same original", permit, reviews=records)


def test_changed_review_manifest_cannot_reuse_old_cached_approval(sample):
    raw, doc, permit, records = sample
    result = apply_recovery_review(doc, raw, permit, reviews=records)
    changed = copy.deepcopy(records)
    changed["11790-1"]["conditions"][0]["subject_scope"] = "different group"
    assert not recovery_review_is_current(result, permit, changed)


@pytest.mark.parametrize("changed_url", [False, True])
def test_failed_refresh_retains_reviewed_original_only_for_exact_registry_url(sample, monkeypatch, changed_url):
    from wattdocket.production.pipeline import retain_virginia_document_evidence
    raw, doc, permit, records = sample
    monkeypatch.setattr("wattdocket.production.permit_recovery.reviewed_records", lambda: records)
    approved = apply_recovery_review(doc, raw, permit, reviews=records)
    approved["retrieved_at"] = "2026-11-05T02:25:00Z"
    previous = {"result": {"data": {"permits": [{**permit, "document": approved}]}}}
    candidate = {**permit, "permit_url": URL + "7" if changed_url else URL,
                 "document": {"status": "failed", "error": "HTTP403; reader cannot replace original PDF"}}
    result = {"source_id": "virginia_permits", "data": {"permits": [candidate]}}
    retain_virginia_document_evidence(result, previous, "2026-12-06T02:00:00Z")
    current = result["data"]["permits"][0]["document"]
    if changed_url:
        assert current["freshness"] == "unavailable" and not current.get("obligations")
    else:
        assert current["freshness"] == "stale" and current["obligations"][0]["value"] == 583600
        assert current["retrieved_at"] == "2026-11-05T02:25:00Z"
        assert current["latest_fetch_error"].startswith("HTTP403")


def test_native_pdf_limits_do_not_turn_scanned_or_encrypted_files_into_text(sample):
    raw, *_ = sample
    with pytest.raises(ValueError, match="bounded original PDF"):
        native_pdf_pages(b"<html>Access denied</html>")
    doc = pymupdf.open(stream=raw, filetype="pdf")
    encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="test")
    with pytest.raises(ValueError, match="encrypted"):
        native_pdf_pages(encrypted)


def test_pdf_transport_profile_is_scoped_and_receipt_records_real_headers(tmp_path, monkeypatch):
    from email.message import Message
    calls = []
    class Response(io.BytesIO):
        url = URL
        headers = Message()
        headers["Content-Type"] = "application/pdf"
    def open_request(request, **kwargs):
        calls.append(dict(request.header_items()))
        return Response(b"%PDF-public original test bytes")
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    store = LocalStore(tmp_path)
    fetcher = CaptureFetcher(store, datetime(2026,11,5,tzinfo=timezone.utc), attempts=1)
    fetcher.get_publisher_pdf(URL)
    receipt = fetcher.receipt(URL)
    assert calls[0]["User-agent"] == "Mozilla/5.0"
    assert calls[0]["Accept"] == "application/pdf"
    assert receipt["request_profile"] == "deq_public_pdf"
    assert receipt["request_headers"]["User-Agent"] == "Mozilla/5.0"
    with pytest.raises(ValueError, match="restricted"):
        fetcher.get_publisher_pdf("https://example.org/private.pdf")
    assert len(calls) == 1
