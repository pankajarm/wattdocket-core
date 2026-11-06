"""Independent checks on emitted evidence before immutable publication.

This gate intentionally does not import the matching or geometry implementation.
It checks the public assertions against their published identifiers and scopes;
it does not claim independent physical-site or legal-effectiveness validation.
"""
from __future__ import annotations

import re
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse


class RecordEvidenceError(ValueError):
    """A release makes an unsupported or internally inconsistent assertion."""


def _require(condition, record_id, reason):
    if not condition:
        raise RecordEvidenceError(f"{record_id}: {reason}")


def _permit_identity(metadata, matched_pairs, record_id):
    registration = str(metadata.get("registration") or "")
    _require(bool(re.fullmatch(r"\d{4,6}-\d+", registration))
             and (metadata.get("county_fips"), registration.split("-")[0]) in matched_pairs,
             record_id, "Permit county/registration conflicts with matched AIR identity")


def validate_record_evidence(cards: dict, entries: list[dict]) -> None:
    """Fail closed on known identity/location/measurement publication failures."""
    _require(isinstance(cards, dict) and isinstance(entries, list), "release", "Invalid public record containers")
    indexed = {entry.get("id"): entry for entry in entries}
    _require(len(indexed) == len(entries) and None not in indexed, "release", "Missing or repeated index identity")
    _require(len(cards) == len(entries), "release", "Card/index record counts differ")
    visited = set()
    for card in cards.values():
        facility = card.get("facility") or {}
        fid = facility.get("id")
        _require(fid in indexed and fid not in visited, fid, "Card identity missing or duplicated in index")
        visited.add(fid)
        entry = indexed[fid]
        verified = facility.get("class_verified")
        _require(isinstance(verified, bool) and entry.get("class_verified") == verified, fid, "Classification evidence differs between index and card")
        identity = facility.get("identity_match") or {}
        matched_ids = identity.get("matched_source_ids", [])
        _require(isinstance(matched_ids, list), fid, "Matched source identifiers must be a list")
        parsed = [re.fullmatch(r"VA00000051(\d{3})(\d{5})", value) if isinstance(value, str) else None for value in matched_ids]
        pairs = {(match.group(1), match.group(2)) for match in parsed if match}
        if verified:
            if identity.get("method") == "reviewed_deq_icis_cross_reference":
                for link in identity.get("reviewed_links", []):
                    expected = link.get("expected_record") or {}
                    air = expected.get("air_record") or {}
                    proof = link.get("evidence") or []
                    gis = next((e.get("record") for e in proof if e.get("record", {}).get("PLA_ICIS_ID")), {})
                    _require(link.get("record_id") == expected.get("id") == fid
                        and link.get("verification_status") == "verified" and link.get("relationship") == "same_regulator_record"
                        and air.get("SourceID") in matched_ids and air.get("SourceID") == gis.get("PLA_ICIS_ID")
                        and fid == "echo:" + str(air.get("RegistryID"))
                        and str(gis.get("PLA_REG_NUM")) in re.findall(r"\d+", str(air.get("OtherPermitIDs", "")))
                        and len(proof) >= 2, fid, "Reviewed regulator cross-reference is incomplete")
                    for evidence in proof:
                        receipt = evidence.get("receipt") or {}
                        _require(receipt.get("url") == evidence.get("url") and receipt.get("sha256") == evidence.get("sha256")
                            and re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("sha256", "")))
                            and receipt.get("raw_key") == "raw/" + str(evidence.get("sha256")) + ".gz"
                            and bool(receipt.get("retrieved_at")), fid, "Reviewed identity lacks original receipt")
                    pairs.add((str(air.get("AIRFIPSCode"))[2:], str(gis["PLA_REG_NUM"])))
            _require(identity.get("status") == "matched" and identity.get("method") in {"county_and_registration", "reviewed_deq_icis_cross_reference"},
                     fid, "Verified classification lacks county-and-registration identity match")
            _require(bool(parsed) and all(parsed), fid, "Verified classification lacks valid matched AIR identifiers")
            _require(facility.get("state") in (None, "VA"), fid, "Virginia regulator identity attached outside Virginia")
            summaries = [row.get("value", "") for row in facility.get("record_summary", []) if row.get("label") == "AIR program ID"]
            if summaries:
                published_ids = set(re.findall(r"\bVA00000051\d{8}\b", " ".join(map(str, summaries))))
                _require(set(matched_ids) <= published_ids, fid, "Matched identity unsupported by published AIR program IDs")

        for record in (entry, facility):
            site = record.get("site_identity") or {}
            _require(record.get("site_id") is None and site.get("physical_site_id") is None
                     and site.get("merge_verified") is False, fid, "Source record promoted to verified physical-site grouping")
            for group in record.get("duplicate_candidates", []):
                _require(group.get("merge_verified") is False, fid, "Candidate duplicate group presented as verified merge")
        location = facility.get("location_quality") or {}
        _require(entry.get("location_quality") == location, fid, "Index/card location review differs")
        _require(isinstance(location.get("map_eligible"), bool) and location.get("physical_site_verified") is False,
                 fid, "Missing location review or unsupported physical-site verification")
        claims = card.get("claims") or []
        scenarios = [claim for claim in claims if str(claim.get("id", "")).startswith("scenario-")
                     or claim.get("scope") == "scenario" or claim.get("range_kind") == "scenario"
                     or (claim.get("inputs") or {}).get("input_source") == "illustrative_preset"]
        osm_identity = re.fullmatch(r"osm:(node|way|relation):\d+", str(fid))
        if osm_identity:
            kind = osm_identity[1]
            evidence = location.get("evidence") or {}
            expected_source = "osm_node" if kind == "node" else "osm_bounding_box_center"
            _require(evidence.get("osm_element_type") == kind and evidence.get("coordinate_source") == expected_source,
                     fid, "OSM coordinate derivation conflicts with the published element identifier")
            if kind != "node":
                grid = card.get("grid") or {}
                _require(location["map_eligible"] is False and grid.get("subregion") is None
                         and grid.get("rate_lb_per_mwh") is None and not grid.get("alternative") and not scenarios,
                         fid, "OSM area bounding-box center promoted to a map pin, grid binding or scenario")
        if not location["map_eligible"]:
            _require((card.get("grid") or {}).get("subregion") is None and not scenarios,
                     fid, "Withheld location still has a regional binding or scenario")
        for claim in scenarios:
            _require(claim.get("scope") == claim.get("range_kind") == "scenario" and claim.get("badge") == "MODELED",
                     fid, "Scenario presented as a reported measurement")
            _require((claim.get("inputs") or {}).get("facility_measurement") is False
                     and claim.get("facility_measurement") is not True and claim.get("actual_measurement") is not True,
                     fid, "Scenario inputs claim an actual facility measurement")
            bounds = claim.get("range")
            if bounds is not None:
                _require(isinstance(bounds, dict) and set(bounds) == {"low", "base", "high"},
                         fid, "Scenario bounds are percentiles or have an unknown scope")

        limits = facility.get("permit_limits") or []
        diesel = [claim for claim in claims if claim.get("id") == "diesel-mw"]
        if not verified:
            _require(not limits and all(claim.get("value") == "NOT_PUBLISHED" for claim in diesel),
                     fid, "Unverified identity has attached Virginia permit facts")
        for claim in diesel:
            if claim.get("value") != "NOT_PUBLISHED":
                _permit_identity(claim.get("permit_metadata") or {}, pairs, fid)
                _require(claim.get("evidence_scope") == "equipment_table_page" and claim.get("is_complete_fleet") is False,
                         fid, "Generator table promoted to whole-site capacity")
        for limit in limits:
            _permit_identity(limit, pairs, fid)
            _require(limit.get("scope") == "quoted_condition" and limit.get("applies_to_entire_site") is False,
                     fid, "Quoted permit condition promoted to a whole-site constraint")
            # No collector currently establishes legal effect. A boolean or a
            # successful quote/hash check is not independent legal evidence.
            _require(limit.get("effective_status") == "unknown"
                     and limit.get("latest_effective_status_verified") is False
                     and limit.get("is_current_effective_limit") in (None, False),
                     fid, "Unsupported assertion of current permit effectiveness")


def _disclosure_https(value):
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _disclosure_words(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _disclosure_address(value):
    suffixes = {"street": "st", "road": "rd", "boulevard": "blvd", "parkway": "pkwy",
                "lane": "ln", "avenue": "ave", "drive": "dr", "circle": "cir"}
    return " ".join(suffixes.get(word, word) for word in _disclosure_words(value).split())


def validate_disclosure_publication(payload: dict, cards: dict, coverage: dict) -> None:
    """Independently check emitted reporting entities, identities and card refs.

    No imports from the disclosure builder, resolver or source adapters. This
    checks public evidence consistency; it is not a second field survey or a
    verification of the operator's meters. The external release verifier also
    checks the retained source objects against their declared hashes.
    """
    label = "disclosures"
    _require(isinstance(payload, dict) and isinstance(cards, dict), label, "Invalid disclosure containers")
    locations = payload.get("locations")
    _require(isinstance(locations, list), label, "Missing reporting entities")
    by_id, observations, emitted_links = {}, {}, []
    by_card = {}
    for card in cards.values():
        fid = (card.get("facility") or {}).get("id")
        _require(fid and fid not in by_card, label, "Duplicate or missing inventory card identity")
        by_card[fid] = card
        _require(card.get("release_id") == payload.get("release_id"), fid, "Disclosure/card release differs")
    evidence = {}
    for item in payload.get("identity_evidence", []):
        eid, sha, receipt = item.get("id"), item.get("content_sha256"), item.get("receipt") or {}
        _require(eid and eid not in evidence, label, "Duplicate or missing identity evidence ID")
        _require(isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha), eid, "Identity evidence hash invalid")
        _require(item.get("verified") is True and _disclosure_https(item.get("url")) and item.get("publisher"),
                 eid, "Identity evidence lacks verified primary attribution")
        _require(receipt.get("sha256") == sha and receipt.get("raw_key") == f"raw/{sha}.gz"
                 and receipt.get("url") == item["url"] and receipt.get("retrieved_at") == item.get("retrieved_at")
                 and bool(item.get("retrieved_at")), eid, "Identity evidence receipt does not support the published passage")
        _require(isinstance(item.get("excerpts"), list) and item["excerpts"] and all(isinstance(q, str) and q.strip() for q in item["excerpts"]),
                 eid, "Identity evidence passages missing")
        if item.get("representation") == "publisher_pdf":
            _require(isinstance(item.get("page"), int) and not isinstance(item.get("page"), bool) and item["page"] > 0,
                     eid, "Identity PDF page missing")
        evidence[eid] = item
    valid_metrics = {"electricity_consumption", "water_withdrawal", "water_use_reported", "water_consumption", "water_discharge",
                     "emissions_scope1", "emissions_scope2_location", "emissions_scope2_market",
                     "emissions_scope1_and2_market", "reported_ghg_total"}
    valid_scopes = {"campus", "building", "regulatory_facility", "operator_location"}
    for location in locations:
        lid = location.get("disclosure_location_id")
        _require(isinstance(lid, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:._-]{0,180}", lid) and lid not in by_id,
                 label, "Invalid or duplicate disclosure entity")
        _require(location.get("reporting_scope") in valid_scopes and location.get("location_label") and location.get("source_id"),
                 lid, "Disclosure entity scope, label or source missing")
        by_id[lid] = location
        _require(isinstance(location.get("observations"), list) and isinstance(location.get("record_links"), list),
                 lid, "Observation or relationship container missing")
        for row in location["observations"]:
            oid, metric, value = row.get("observation_id"), row.get("metric"), row.get("value")
            _require(isinstance(oid, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:._-]{0,180}", oid) and oid not in observations,
                     lid, "Invalid or repeated observation identity")
            _require(row.get("disclosure_location_id") == lid and row.get("source_id") == location["source_id"]
                     and row.get("reporting_scope") == location["reporting_scope"], oid, "Observation entity, source or scope differs")
            _require(metric in valid_metrics and isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value) and value >= 0, oid, "Unsupported metric or invalid numeric observation")
            unit = str(row.get("unit", "")).lower()
            allowed = ({"mwh", "kwh", "gwh", "kbtu"} if metric == "electricity_consumption" else
                       {"million gallons", "million liters", "million litres", "megaliters", "megalitres", "ml", "m3", "m³",
                        "cubic meters", "cubic metres", "us gallons", "gallons", "kgal", "thousand gallons"}
                       if metric.startswith("water_") else
                       {"metric tons co2e", "metric tonnes co2e", "tonnes co2e", "t co2e", "metric tons co₂e", "metric tonnes co₂e"})
            _require(unit in allowed, oid, "Reported observation has the wrong metric unit")
            year = row.get("data_year")
            _require(isinstance(year, int) and not isinstance(year, bool) and 1990 <= year <= datetime.now(timezone.utc).year,
                     oid, "Observation reporting year invalid")
            _require(row.get("period_start") == f"{year}-01-01T00:00:00Z" and row.get("period_end") == f"{year+1}-01-01T00:00:00Z",
                     oid, "Observation year and reporting period differ")
            sha = row.get("source_sha256")
            _require(isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha) and _disclosure_https(row.get("source_url"))
                     and row.get("publisher") and (row.get("source_page") or row.get("source_field") or row.get("source_quote")),
                     oid, "Observation source evidence or locator missing")
            _require(row.get("reporting_basis") and row.get("methodology_note") and row.get("retrieved_at"),
                     oid, "Observation reporting basis, limitations or retrieval missing")
            receipt = row.get("source_receipt")
            _require(isinstance(receipt, dict) and receipt.get("url") == row["source_url"]
                     and receipt.get("sha256") == sha and receipt.get("raw_key") == f"raw/{sha}.gz"
                     and receipt.get("retrieved_at") == row["retrieved_at"],
                     oid, "Observation retrieval or source bytes differ from the immutable capture receipt")
            _require(row.get("inventory_allocation") is False and row.get("facility_binding_verified") is False
                     and row.get("value_origin") == "reported_disclosure", oid, "Source observation allocated to an inventory record")
            from .municipal_quality import validate_municipal_observation, MunicipalEvidenceError
            try:
                validate_municipal_observation(location, row)
            except MunicipalEvidenceError as exc:
                _require(False, oid, str(exc))
            observations[oid] = row
        seen_records = set()
        for link in location["record_links"]:
            fid, relation = link.get("record_id"), link.get("relationship")
            _require(fid and fid not in seen_records and link.get("disclosure_location_id") == lid,
                     lid, "Repeated or misdirected record link")
            seen_records.add(fid)
            _require(relation in {"within_disclosure_location", "same_physical_scope", "candidate"}, lid, "Unknown coverage relationship")
            verified = link.get("verification_status") == "verified"
            _require(isinstance(link.get("inventory_allocation"), bool), fid, "Allocation flag missing or nonboolean")
            allocated = link.get("inventory_allocation") is True
            permitted = verified and relation == "same_physical_scope" and link.get("allocation_allowed") is True and link.get("same_physical_scope_verified") is True
            _require(allocated == permitted, fid, "Numeric allocation lacks an explicitly verified equal boundary")
            if verified and relation == "same_physical_scope":
                equivalence = link.get("scope_equivalence") or {}
                boundary_ids = equivalence.get("evidence_ids") or []
                _require(equivalence.get("verified") is True and equivalence.get("basis") == "exact_reporting_boundary"
                         and isinstance(boundary_ids, list) and boundary_ids
                         and all(key in evidence and key in link.get("evidence_ids", []) for key in boundary_ids),
                         fid, "Equal reporting scope lacks explicit boundary-equivalence evidence")
            if relation != "same_physical_scope":
                _require(link.get("allocation_allowed") is False and link.get("same_physical_scope_verified") is False,
                         fid, "Campus/building correspondence promoted to equal scope")
            if verified:
                _require(relation != "candidate" and fid in by_card, fid, "Verified identity points to an absent record or candidate")
                expected, actual = link.get("expected_record") or {}, by_card[fid]["facility"]
                _require(expected.get("id") == fid and actual.get("id") == fid, fid, "Reviewed stable record identity changed")
                if re.fullmatch(r"osm:(node|way|relation):\d+", fid):
                    _require(expected.get("registry_id") is None and actual.get("registry_id") is None
                             and expected.get("program_id") is None,
                             fid, "OSM coverage link falsely claims an EPA registry/program identity")
                else:
                    _require(expected.get("registry_id") and fid == "echo:" + str(expected["registry_id"])
                             and str(actual.get("registry_id")) == str(expected["registry_id"]),
                             fid, "Reviewed registry identity differs from emitted card")
                for key in ("name", "city", "state"):
                    _require(expected.get(key) and _disclosure_words(expected[key]) == _disclosure_words(actual.get(key)),
                             fid, f"Reviewed {key} differs from emitted card")
                _require(expected.get("address") and _disclosure_address(expected["address"]) == _disclosure_address(actual.get("address")),
                         fid, "Reviewed physical address differs from emitted card")
                if expected.get("program_id"):
                    ids = {token for summary in actual.get("record_summary", []) if str(summary.get("label", "")).endswith("program ID")
                           for token in re.findall(r"[A-Z0-9]+", str(summary.get("value") or "").upper())}
                    _require(expected["program_id"] in ids, fid, "Reviewed regulator identifier absent from emitted card")
                ids = link.get("evidence_ids")
                _require(isinstance(ids, list) and ids and len(ids) == len(set(ids)) and all(x in evidence for x in ids),
                         fid, "Coverage link lacks unique captured primary evidence")
                _require(link.get("evidence") == [evidence[x] for x in ids], fid, "Link evidence differs from published receipts")
            emitted_links.append((lid, link))

    expected_refs, exact = defaultdict(set), {key: set() for key in ("power", "water", "carbon")}
    for lid, link in emitted_links:
        if link.get("verification_status") == "verified":
            expected_refs[link["record_id"]].add(lid)
            if link["inventory_allocation"]:
                for row in by_id[lid]["observations"]:
                    family = "power" if row["metric"] == "electricity_consumption" else "water" if row["metric"].startswith("water_") else "carbon"
                    exact[family].add(link["record_id"])
    for fid, card in by_card.items():
        found = set()
        for reference in card.get("disclosures", []):
            location, link = reference.get("location") or {}, reference.get("link") or {}
            lid = location.get("disclosure_location_id")
            _require(lid in expected_refs[fid] and lid not in found, fid, "Card has an unverified or repeated disclosure reference")
            _require(location == by_id[lid] and link in by_id[lid]["record_links"] and link.get("record_id") == fid,
                     fid, "Card disclosure or link differs from canonical reporting entity")
            found.add(lid)
        _require(found == expected_refs[fid], fid, "Verified disclosure omitted from card")
        for claim in card.get("claims", []):
            if claim.get("scope") == "facility" and claim.get("id") in {"power-mw", "water-gal", "facility-carbon"} and isinstance(claim.get("value"), (int, float)):
                family = {"power-mw": "power", "water-gal": "water", "facility-carbon": "carbon"}[claim["id"]]
                _require(fid in exact[family], fid, "Related disclosure promoted to an actual facility measurement")
                _require(claim.get("observation_id") in observations, fid, "Actual claim lacks a canonical observation")
                row = observations[claim["observation_id"]]
                _require(claim.get("value") == row["value"] and claim.get("unit") == row["unit"], fid, "Annual observation converted or relabeled as a different facility quantity")
    metric_counts = dict(Counter(row["metric"] for row in observations.values()))
    _require(payload.get("observation_count") == len(observations) and payload.get("metric_counts") == metric_counts,
             label, "Observation counts differ from emitted evidence")
    expected_coverage = {"disclosure_locations": len(by_id), "disclosure_observations": len(observations),
                         "records_with_linked_disclosures": sum(bool(x) for x in expected_refs.values()),
                         "reviewed_disclosure_links": sum(x.get("verification_status") == "verified" for _, x in emitted_links),
                         **{f"actual_{family}_records": len(ids) for family, ids in exact.items()}}
    for family in ("electricity", "water", "emissions"):
        expected_coverage[f"reported_{family}_locations"] = sum(any(
            row["metric"] == "electricity_consumption" if family == "electricity" else row["metric"].startswith("water_")
            if family == "water" else row["metric"].startswith("emissions_") or row["metric"] == "reported_ghg_total"
            for row in location["observations"]) for location in locations)
    for key, value in expected_coverage.items():
        _require(type(payload.get("coverage", {}).get(key)) is int and type(coverage.get(key)) is int
                 and payload["coverage"][key] == value and coverage[key] == value,
                 label, f"Coverage count differs from emitted evidence: {key}")
