"""Independent reconciliation of public recovery reviews, cards and citations.

Original PDF bytes and visible transcription checks are a separate archive audit.
No ingestion or extraction code is imported here.
"""
import re
from datetime import datetime

REVIEWED_IDENTITY_METHOD = "reviewed_deq_icis_cross_reference"


def _captured(evidence):
    receipt = evidence.get("receipt") or {}
    digest = evidence.get("sha256", "")
    return (str(evidence.get("url", "")).startswith("https://")
            and bool(re.fullmatch(r"[a-f0-9]{64}", str(digest)))
            and receipt.get("url") == evidence.get("url") and receipt.get("sha256") == digest
            and receipt.get("raw_key") == "raw/" + digest + ".gz"
            and bool(receipt.get("retrieved_at")) and bool(evidence.get("locator")))


def reviewed_identity_supported(facility, permit):
    """Reconcile the published crosswalk without trusting its 'verified' label."""
    site = permit.get("site_identity_review") or {}
    root = str(permit.get("registration", "")).split("-")[0]
    if site.get("status") != "regulator_site_verified" or site.get("registration_root") != root:
        return False
    gis = [e for e in site.get("evidence", []) if _captured(e)
           and str((e.get("record") or {}).get("PLA_REG_NUM")) == root]
    if len(gis) != 1:
        return False
    record = gis[0]["record"]
    for link in site.get("inventory_links", []):
        expected = link.get("expected_record") or {}
        air = expected.get("air_record") or {}
        if (link.get("record_id") == expected.get("id") == facility.get("id") == "echo:" + str(air.get("RegistryID"))
                and link.get("relationship") == "same_regulator_record" and link.get("verification_status") == "verified"
                and link.get("method") == REVIEWED_IDENTITY_METHOD
                and air.get("SourceID") == record.get("PLA_ICIS_ID")
                and root in re.findall(r"\d+", str(air.get("OtherPermitIDs", "")))
                and str(air.get("AIRFIPSCode")) == "51" + str(permit.get("county_fips"))
                and air.get("AIRState") == record.get("FAC_L_STATE") == "VA"
                and air.get("AIRStreet") and air.get("AIRCity")
                and len(link.get("evidence", [])) >= 2 and all(_captured(e) for e in link["evidence"])
                and link in facility.get("identity_match", {}).get("reviewed_links", [])):
            return True
    return False


def validate_permit_context(row, checks):
    label = "Reviewed context " + row["registration"] + ": "
    history = row.get("permit_history_review")
    site = row.get("site_identity_review")
    if history:
        checks.require(history.get("review_version") == "va-history-1"
                       and history.get("status") in {"verified", "review_required", "unavailable"}, label + "invalid history review")
        checks.require(history.get("registration") == row.get("registration")
                       and history.get("publisher_url") == row.get("url")
                       and history.get("document_sha256") == row.get("content_sha256"), label + "history belongs to a different document")
        legal = history.get("legal_effectiveness") or {}
        checks.require(legal.get("status") in {"unknown", "source_confirmed_current", "superseded"}, label + "unknown legal status type")
        if legal.get("status") != "unknown":
            checks.require(history.get("status") == "verified" and legal.get("explicit_exact_document_or_condition_assertion") is True
                           and legal.get("scope") in {"entire_document", "specified_conditions"}
                           and bool(legal.get("evidence")) and all(_captured(e) and e.get("quote") for e in legal["evidence"]),
                           label + "legal conclusion lacks explicit scoped original evidence")
            if legal.get("scope") == "specified_conditions":
                checks.require(bool(legal.get("condition_refs")), label + "legal assertion has no specified conditions")
        for event in history.get("events", []):
            checks.require(bool(event.get("scope")) and bool(event.get("evidence"))
                           and all(_captured(e) for e in event["evidence"]), label + "history event lacks captured evidence")
    if site:
        checks.require(site.get("review_version") == "va-site-1"
                       and site.get("registration_root") == row["registration"].split("-")[0], label + "site review belongs to another registration")
        if site.get("status") == "regulator_site_verified":
            gis = [e for e in site.get("evidence", []) if _captured(e) and isinstance(e.get("record"), dict)]
            checks.require(len(gis) == 1, label + "verified physical site has no unique captured regulator row")
            if len(gis) == 1:
                source = gis[0]["record"]
                address = site.get("physical_address") or {}
                checks.require(str(source.get("PLA_REG_NUM")) == site["registration_root"]
                               and site.get("regulator_site_id") == "va-deq:" + str(source.get("PLA_ID")), label + "regulator site ID mismatch")
                checks.require(address == {"street": source.get("FAC_L_ADDR_1"), "city": source.get("FAC_L_CITY"),
                                           "state": source.get("FAC_L_STATE"), "postal_code": source.get("FAC_L_ZIP5")}, label + "physical address differs from regulator row")
                status = site.get("site_status") or {}
                checks.require(status.get("status_group") == source.get("STATUS_GROUP")
                               and status.get("air_operating_status") == source.get("AIR_OP_STATUS"), label + "site operating status differs from source")


def validate_permit_recovery(objects, coverage, checks):
    rows = objects["data-status.json"].get("permit_recovery", {}).get("documents", [])
    by_reg = {p.get("registration"): p for p in rows}
    checks.require(len(rows) == len(by_reg), "Duplicate recovered permit review")
    checks.require(coverage.get("reviewed_permit_documents", 0) == len(rows), "Recovered document count differs")
    for row in rows:
        validate_permit_context(row, checks)
        site = row.get("site_identity_review") or {}
        if site.get("status") == "regulator_site_verified":
            try:
                built = datetime.fromisoformat(objects["data-status.json"]["built_at"].replace("Z", "+00:00"))
                captured = datetime.fromisoformat(site["retrieved_at"].replace("Z", "+00:00"))
                checks.require(0 <= (built - captured).total_seconds() <= 3 * 86400,
                               "Regulator identity proof exceeds three-day capture window: " + row["registration"])
                source = next(s for s in objects["data-status.json"]["sources"] if s["id"] == "virginia_site_registry")
                checks.require(site.get("source_freshness") == source.get("status") in {"current", "stale"},
                               "Site identity hides source freshness: " + row["registration"])
            except (KeyError, ValueError, TypeError, StopIteration):
                checks.require(False, "Site identity lacks bounded, dated source proof: " + row["registration"])
        review = row.get("recovery_review") or {}
        label = "Recovered permit " + str(row.get("registration")) + ": "
        checks.require(row.get("parse_status") == "parsed" and row.get("extraction_version") == "va-reviewed-pdf-1"
                       and review.get("status") == "verified" and review.get("review_version") == "va-reviewed-pdf-1",
                       label + "missing reviewed extraction status")
        checks.require(bool(re.fullmatch(r"[a-f0-9]{64}", str(row.get("content_sha256", ""))))
                       and row.get("content_sha256") == review.get("document_sha256")
                       and row.get("registration") == review.get("registration")
                       and row.get("url") == review.get("publisher_url"), label + "review/document identity mismatch")
        checks.require(row.get("representation") in {"publisher_pdf", "reviewed_pdf_transcription"}
                       and bool(row.get("retrieved_at")), label + "missing original PDF capture")
        checks.require(row.get("effective_status") == "unknown" and row.get("latest_effective_status_verified") is False
                       and review.get("supersession_review", {}).get("current_legal_effectiveness") == "unknown",
                       label + "recovery promoted to current legal effectiveness")
        listing = review.get("registry_listing") or {}
        checks.require(listing.get("county_fips") == row.get("county_fips")
                       and listing.get("issued_at") == row.get("issued_at")
                       and bool(listing.get("checked_at")), label + "registry identity/date mismatch")
        for key in ("eligible_condition_pages", "eligible_equipment_pages"):
            pages = review.get(key)
            checks.require(isinstance(pages, list) and all(type(p) is int and p > 0 for p in pages)
                           and len(set(pages)) == len(pages), label + "invalid reviewed page list")
    linked = {}
    card_count = 0
    for card in objects["cards.json"].values():
        facility = card["facility"]
        reviews = facility.get("permit_reviews", [])
        if reviews:
            card_count += 1
        for row in reviews:
            canonical = by_reg.get(row.get("registration"), {})
            checks.require(row == {k: v for k, v in canonical.items() if k != "inventory_record_ids"},
                           "Card review differs from source review: " + facility["id"])
            identity = facility.get("identity_match", {})
            source_id = "VA00000051" + str(row.get("county_fips")) + str(row.get("registration", "")).split("-")[0]
            direct = (identity.get("method") == "county_and_registration" and source_id in identity.get("matched_source_ids", []))
            reviewed = identity.get("method") == REVIEWED_IDENTITY_METHOD and reviewed_identity_supported(facility, row)
            checks.require(identity.get("status") == "matched" and (direct or reviewed),
                           "Recovered document attached by unverified identity: " + facility["id"])
            linked.setdefault(row.get("registration"), []).append(facility["id"])
        for limit in facility.get("permit_limits", []):
            if limit.get("extraction_version") != "va-reviewed-pdf-1":
                continue
            canonical = by_reg.get(limit.get("registration"), {})
            review = canonical.get("recovery_review", {})
            checks.require(limit.get("recovery_review") == review and review.get("status") == "verified"
                           and limit.get("content_sha256") == canonical.get("content_sha256")
                           and limit.get("page") in review.get("eligible_condition_pages", [])
                           and limit.get("page_derivation", "").startswith("PDF page index"),
                           "Condition outside reviewed PDF/pages: " + facility["id"])
        for claim in card.get("claims", []):
            review = claim.get("permit_metadata", {}).get("recovery_review") or {}
            if not review:
                continue
            canonical = by_reg.get(review.get("registration"), {})
            checks.require(review == canonical.get("recovery_review")
                           and all(c.get("doc_sha256") == canonical.get("content_sha256")
                                   and c.get("page") in review.get("eligible_equipment_pages", [])
                                   for c in claim.get("citations", [])), "Equipment outside reviewed PDF/pages: " + facility["id"])
    for registration, row in by_reg.items():
        checks.require(sorted(row.get("inventory_record_ids", [])) == sorted(linked.get(registration, [])),
                       "Recovered document inventory links differ: " + registration)
    checks.require(coverage.get("records_with_reviewed_permits", 0) == card_count, "Reviewed permit record count differs")
