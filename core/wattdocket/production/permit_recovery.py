"""Pinned visual review of recovered public permit PDFs.

Embedded OCR is not assumed correct. Reviewed numeric facts are admitted only
against an exact PDF version and physical page; original and corrected text
hashes remain separate from the original PDF hash. No OCR service is invoked.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

REVIEW_VERSION = "va-reviewed-pdf-1"
MANIFEST = Path(__file__).with_name("data") / "virginia_recovery_reviews.json"
MAX_PDF_BYTES = 16_000_000
MAX_PDF_PAGES = 200
MAX_TEXT_CHARS = 2_000_000
EXTENTS = {"full_permit", "partial_amendment", "correspondence", "permit_letter", "unknown"}


def normalized(value):
    return " ".join(value.split())


def sha(value):
    return hashlib.sha256(value).hexdigest()


def native_pdf_pages(raw):
    import pymupdf
    if not raw.startswith(b"%PDF-") or len(raw) > MAX_PDF_BYTES:
        raise ValueError("Recovered permit is not a bounded original PDF")
    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        if doc.needs_pass or not 0 < len(doc) <= MAX_PDF_PAGES:
            raise ValueError("Recovered PDF is encrypted or exceeds page bound")
        pages, length = {}, 0
        for index, page in enumerate(doc):
            text = normalized(page.get_text())
            length += len(text)
            if length > MAX_TEXT_CHARS:
                raise ValueError("Recovered native text exceeds bound")
            pages[index + 1] = text
    return pages


def reviewed_records():
    if not MANIFEST.is_file():
        return {}
    data = json.loads(MANIFEST.read_bytes())
    if data.get("review_version") != REVIEW_VERSION:
        raise ValueError("Unknown permit review manifest version")
    return data["documents"]


def recovery_review_is_current(document, permit, reviews=None):
    records = reviewed_records() if reviews is None else reviews
    spec = records.get(permit.get("registration"))
    review = document.get("recovery_review") or {}
    if spec is None:
        return document.get("extraction_version") == "va-deterministic-3"
    return (document.get("extraction_version") == REVIEW_VERSION and review.get("status") == "verified"
            and review.get("manifest_record_sha256") == sha(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode())
            and spec.get("publisher_url") == permit.get("permit_url")
            and spec.get("document_sha256") == document.get("content_sha256"))


def apply_recovery_review(document, raw, permit, *, registry_receipt=None, reviews=None):
    """Replace automated extraction with the reviewed, page-scoped facts.

    A changed source version remains readable, but its numerical evidence is
    withdrawn pending visual review. The registry link and current legal effect
    are separate; this function never upgrades either to an inferred status.
    """
    records = reviewed_records() if reviews is None else reviews
    spec = records.get(permit.get("registration"))
    if spec is None:
        return document
    result = copy.deepcopy(document)
    result.update(obligations=[], equipment=[], extraction_version=REVIEW_VERSION)
    if not raw.startswith(b"%PDF-"):
        raise ValueError("Reviewed permit requires original PDF bytes; reader fallback does not establish a changed document")
    if (spec.get("registration") != permit.get("registration")
            or spec.get("publisher_url") != permit.get("permit_url")
            or spec.get("document_sha256") != sha(raw)):
        result["recovery_review"] = {"status": "review_required", "review_version": REVIEW_VERSION,
            "registration": permit.get("registration"), "publisher_url": permit.get("permit_url"),
            "document_sha256": sha(raw), "current_legal_effectiveness": "unknown",
            "reason": "Original document URL or bytes changed; previous visual numeric approval withdrawn."}
        return result
    pages = native_pdf_pages(raw)
    if len(pages) != spec.get("page_count") or spec.get("document_extent") not in EXTENTS:
        raise ValueError("Recovered document page count/extent differs from reviewed evidence")
    corrected, corrections = dict(pages), []
    for c in spec.get("corrections", []):
        page, old, new = c["pdf_page"], normalized(c["old_text"]), normalized(c["new_text"])
        if (page not in pages or not old or old == new or corrected[page].count(old) != 1
                or not re.fullmatch(r"[0-9a-f]{64}", c.get("visual_review_sha256", ""))):
            raise ValueError("Reviewed transcription does not uniquely match the pinned original page")
        corrected[page] = corrected[page].replace(old, new, 1)
        corrections.append({**c, "native_page_text_sha256": sha(pages[page].encode()),
                            "reviewed_page_text_sha256": sha(corrected[page].encode())})
    for field, evidence in spec.get("identity_review", {}).items():
        if evidence is None or not isinstance(evidence, dict) or not evidence.get("quote"):
            continue
        page = evidence["pdf_page"]
        if page not in corrected or normalized(evidence["quote"]) not in corrected[page]:
            raise ValueError("Reviewed identity quotation is absent from original PDF page: " + field)
    reg_evidence = spec.get("identity_review", {}).get("registration") or {}
    root = permit["registration"].split("-")[0]
    if str(reg_evidence.get("value")) != root or not re.search(r"(?<!\d)" + root + r"(?!\d)", reg_evidence.get("quote", "")):
        raise ValueError("Recovered document lacks its exact reviewed registration identity")
    historical = spec.get("supersession_review") or {}
    if historical.get("current_legal_effectiveness") != "unknown":
        raise ValueError("A recovery review cannot assert current legal effectiveness")
    for statement in historical.get("statements", []):
        if normalized(statement["quote"]) not in corrected.get(statement["pdf_page"], ""):
            raise ValueError("Historical replacement quotation is absent from the cited PDF page")
    allowed_conditions = set(spec.get("eligible_condition_pages", []))
    allowed_equipment = set(spec.get("eligible_equipment_pages", []))
    if not (allowed_conditions | allowed_equipment) <= set(pages):
        raise ValueError("Reviewed numeric page exceeds original PDF")

    def citation(item, eligible):
        page, quote = item["pdf_page"], normalized(item["quote"])
        if page not in eligible or not quote or quote not in corrected[page]:
            raise ValueError("Reviewed numerical quotation is not on an eligible source page")
        is_transcribed = quote not in pages[page]
        if is_transcribed and not any(c["pdf_page"] == page for c in corrections):
            raise ValueError("Numerical transcription lacks a pinned visual correction")
        return {"document": "Virginia air permit " + permit["registration"],
                "document_id": "VA DEQ registration " + root,
                "publisher": "Virginia Department of Environmental Quality", "page": page,
                "quote": quote, "url": permit["permit_url"], "retrieved_via": permit["permit_url"],
                "verbatim_verified": not is_transcribed,
                "content_sha256": sha(raw), "document_sha256": sha(raw),
                "representation": "reviewed_pdf_transcription" if is_transcribed else "publisher_pdf",
                "page_derivation": "PDF page index", "review_version": REVIEW_VERSION,
                "native_page_text_sha256": sha(pages[page].encode()),
                "reviewed_page_text_sha256": sha(corrected[page].encode()),
                "visual_reviewed": True}

    from .identity import condition_metadata
    for item in spec.get("conditions", []):
        if isinstance(item.get("value"), bool) or not isinstance(item.get("value"), (float, int)) or not math.isfinite(item["value"]) or item["value"] < 0:
            raise ValueError("Invalid reviewed numeric condition")
        cite = citation(item, allowed_conditions)
        # Human review fixes the value; the displayed quantity must still exist
        # in the exact reviewed quotation, not merely in a sidecar label.
        token = str(item["value"])
        if float(item["value"]).is_integer():
            token = str(int(item["value"]))
        if not re.search(r"(?<![\d.])" + re.escape(token) + r"(?![\d.])", cite["quote"].replace(",", "")):
            raise ValueError("Reviewed numerical value is absent from its quotation")
        condition = {"metric": item["metric"], "value": item["value"], "unit": item["unit"],
                     "citation": cite, "reviewed_condition_scope": item["subject_scope"],
                     "reviewed_operating_purpose": item.get("purpose"),
                     "reviewed_period": item.get("period"), "review_version": REVIEW_VERSION}
        condition.update(condition_metadata(condition))
        result["obligations"].append(condition)
    for table in spec.get("equipment", []):
        rows = []
        for item in table["rows"]:
            count, rating = item["count"], item["rated_ekw_each"]
            if type(count) is not int or not 1 <= count <= 500 or not isinstance(rating, (int, float)) or isinstance(rating, bool) or not 50 <= rating <= 20000:
                raise ValueError("Invalid reviewed equipment count/rating")
            rows.append({"count": count, "rated_ekw_each": rating, "citation": citation(item, allowed_equipment)})
        if not rows or len({x["citation"]["page"] for x in rows}) != 1:
            raise ValueError("Reviewed equipment must remain a single physical table page")
        total = sum(r["count"] * r["rated_ekw_each"] for r in rows)
        result["equipment"].append({"metric": "generator_units", "value": sum(r["count"] for r in rows),
            "unit": "generators in cited equipment table", "rated_ekw": total, "mw_total": total / 1000,
            "scope": "equipment_table_page", "is_complete_fleet": False, "page": rows[0]["citation"]["page"],
            "rows": len(rows), "citations": [r["citation"] for r in rows], "equipment_rows": rows,
            "reviewed_table_scope": table.get("subject_scope"), "table_note": table.get("note"),
            "table_is_enforceable_condition": False})
    review = {k: copy.deepcopy(v) for k, v in spec.items() if k not in {"conditions", "equipment", "corrections"}}
    review.update(status="verified", review_version=REVIEW_VERSION,
                  manifest_record_sha256=sha(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()),
                  corrections=corrections, current_legal_effectiveness="unknown",
                  registry_listing={"checked_at": (registry_receipt or {}).get("retrieved_at"),
                                    "issued_at": permit.get("issued_iso"), "county": permit.get("county"),
                                    "county_fips": permit.get("county_fips"), "url": permit.get("permit_url"),
                                    "receipt": registry_receipt})
    result.update(recovery_review=review, representation="publisher_pdf", pages_recovered=len(pages),
                  page_numbers=sorted(pages), pages_claimed=len(pages))
    return result
