"""Attach selected Texas documents only through explicit regulator/site identity.

These documents are drafts or technical reviews. Their quoted text is evidence
of what that document says, never a current permit limit or a site measurement.
"""
from __future__ import annotations

import re


def _words(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _address(value):
    text = _words(value)
    if not re.match(r"^\d+\s", text):
        return None
    # Equivalent written street suffixes only; never remove unit/building IDs.
    suffixes = {"road": "rd", "drive": "dr", "street": "st", "avenue": "ave",
                "boulevard": "blvd", "lane": "ln", "parkway": "pkwy"}
    return " ".join(suffixes.get(word, word) for word in text.split())


def _regulator_ids(facility):
    values = [facility.get("regulated_entity_id")]
    values += list((facility.get("program_ids") or {}).values())
    rows = [facility.get("combined_record") or {}, *facility.get("air_records", [])]
    for row in rows:
        values.extend(row.get(key) for key in ("OtherPermitIDs", "RegulatedEntityID", "RN"))
    return {match for value in values for match in re.findall(r"\bRN\d{9}\b", str(value or "").upper())}


def _match(facility, document):
    if str(facility.get("state") or "").upper() != "TX":
        return None
    rn = document.get("regulated_entity_id")
    identifiers = _regulator_ids(facility)
    if rn and rn in identifiers:
        return {"method": "exact_regulated_entity_id", "value": rn}
    # A known conflicting RN defeats a coincident address match.
    if rn and identifiers:
        return None
    identity = document.get("identity") or {}
    address = _address(facility.get("address_line") or facility.get("address"))
    city = _words(facility.get("city"))
    if address and city and address == _address(identity.get("address")) and city == _words(identity.get("city")):
        return {"method": "exact_street_address_city_state", "address": address, "city": city, "state": "TX"}
    return None


def for_facility(facility: dict, texasdata: dict) -> list[dict]:
    """Return independently identified documents with verified quotations only.

    Legacy parsed numeric values are deliberately excluded: a unit identifier
    is not a fleet count, and an applicability threshold is not an engine rating.
    """
    output = []
    for doc in texasdata.get("documents", []):
        match = _match(facility, doc)
        sha = doc.get("content_sha256") or ""
        if not match or doc.get("status") != "parsed" or not re.fullmatch(r"[0-9a-f]{64}", sha):
            continue
        receipt = doc.get("receipt") or {}
        if receipt.get("sha256") != sha or not receipt.get("retrieved_at"):
            continue
        statements, seen = [], set()
        for item in [*doc.get("claims", []), *doc.get("equipment", [])]:
            citation = item.get("citation") or {}
            quote, page = citation.get("quote"), citation.get("page")
            if not citation.get("verified") or citation.get("document_sha256") != sha:
                continue
            if not isinstance(page, int) or not 1 <= page <= doc.get("pages", 0) or not isinstance(quote, str) or not quote.strip():
                continue
            if citation.get("url") != doc.get("url"):
                continue
            # Preserve explicitly stated equipment plans, not inferred counts
            # from identifiers or generic regulatory power-rating thresholds.
            if item in doc.get("equipment", []) and not (
                re.search(r"\b(features|consists|includes|proposes|will)\b", quote, re.I)
                and re.search(r"\bgenerators?\b", quote, re.I)
                and not re.search(r"greater than|less than|EMGEN\d", quote, re.I)
            ):
                continue
            if (page, quote) in seen:
                continue
            seen.add((page, quote))
            statements.append({"quote": quote, "page": page, "url": doc["url"],
                               "content_sha256": sha, "representation": "publisher_pdf",
                               "verified": True, "scope": "document_statement",
                               "is_current_effective_limit": False})
        output.append({"id": doc["id"], "name": doc.get("name"), "publisher": "Texas Commission on Environmental Quality",
                       "permit_number": doc.get("permit_number"), "document_type": doc.get("document_type"),
                       "document_date": doc.get("document_date"), "url": doc["url"],
                       "content_sha256": sha, "representation": "publisher_pdf",
                       "retrieved_at": receipt["retrieved_at"], "receipt": receipt,
                       "identity_match": match, "statements": statements,
                       "has_conflicting_values": bool(doc.get("conflicts")),
                       "is_current_effective_limit": False, "latest_effective_status_verified": False,
                       "note": "Selected draft or technical-review document; current effectiveness is not verified. "
                               + ("Conflicting values appear in this document; no single limit is selected." if doc.get("conflicts") else "Quoted text retains its document scope.")})
    return output
