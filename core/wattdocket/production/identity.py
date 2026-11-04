"""Conservative record linkage and location review; never invent physical sites.

Regulator identity, a physical location, operating status and document legal
effect are separate questions. Exact names and shared addresses produce review
candidates only. Publisher coordinates remain available even when their use as
map pins or regional-calculation inputs must be withheld.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict

from ..classify import VA_COUNTY_FIPS, name_key, source_id_parts

# Census ANSI state codes, used only to interpret EPA's own derived county field.
# https://www.census.gov/library/reference/code-lists/ansi.html
STATE_FIPS = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU AS MP".split(),
    "01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 72 78 66 60 69".split()))
_MAILBOX = re.compile(r"\b(?:P\s*\.?\s*O\s*\.?\s*BOX|POST\s+OFFICE\s+BOX|PMB|PRIVATE\s+MAIL(?:BOX|\s+BOX))\b", re.I)
MAX_SITE_PIN_ACCURACY_M = 1000


def _key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _permit_ref(permit):
    return {"registration": permit.get("registration"), "site": permit.get("site"),
            "county": permit.get("county"), "county_fips": VA_COUNTY_FIPS.get(permit.get("county")),
            "program": permit.get("program"),
            "issued_at": permit.get("issued_iso"), "url": permit.get("permit_url")}


def match_virginia_permits(facility: dict, permits: list[dict]) -> dict:
    """Attach only exact county AND regulator-registration matches.

    Old EPA plant identifiers are not silently translated into a DEQ number.
    A same-name registry entry with a different ID remains an unresolved
    candidate; a county conflict explicitly defeats the name-only candidate.
    """
    result = {"matches": [], "match_basis": None, "matched_source_ids": [], "candidate_matches": [], "conflicts": [],
              "identity_match": {"status": "unmatched", "method": None, "matched_source_ids": [],
                                 "note": "No verified regulator identity link; a source listing is not a distinct physical-site identity."}}
    if str(facility.get("state") or "").upper() != "VA":
        return result
    identifiers = [(r.get("SourceID"), source_id_parts(r.get("SourceID", "")))
                   for r in facility.get("air_records", [])]
    identifiers = [(source, parts) for source, parts in identifiers if parts]
    for permit in permits:
        registration = str(permit.get("registration") or "").split("-")[0]
        county = VA_COUNTY_FIPS.get(permit.get("county"))
        matching = [source for source, parts in identifiers
                    if county and parts == (county, registration)]
        if matching:
            result["matches"].append(permit)
            result["matched_source_ids"].extend(matching)
            continue
        if not name_key(facility.get("name")) or name_key(facility.get("name")) != name_key(permit.get("site")):
            continue
        reasons = []
        if identifiers and county and county not in {parts[0] for _, parts in identifiers}:
            reasons.append("county_conflict")
        if identifiers and registration not in {parts[1] for _, parts in identifiers}:
            reasons.append("registration_not_confirmed")
        candidate = {**_permit_ref(permit), "method": "exact_normalized_name_only",
                     "status": "conflict" if "county_conflict" in reasons else "unverified_candidate",
                     "reason_codes": reasons or ["no_matching_regulator_identifier"],
                     "attaches_facts": False}
        result["candidate_matches"].append(candidate)
        if reasons:
            result["conflicts"].append({"registration": permit.get("registration"),
                                        "reason_codes": reasons,
                                        "source_ids": [source for source, _ in identifiers]})
    if result["matches"]:
        result["matched_source_ids"] = sorted(set(result["matched_source_ids"]))
        result["match_basis"] = "county_and_registration"
        result["identity_match"] = {"status": "matched", "method": "county_and_registration",
                                    "matched_source_ids": result["matched_source_ids"],
                                    "note": "Virginia county and registration match an EPA air-source identifier. This verifies the registry link, not current operation or a unique physical site."}
    elif result["candidate_matches"]:
        rejected = any(x["status"] == "conflict" for x in result["candidate_matches"])
        result["identity_match"] = {"status": "conflict" if rejected else "candidate",
                                    "method": "exact_normalized_name_only",
                                    "matched_source_ids": [],
                                    "note": "Same-name permit entries require identity review; no permit conditions or verified label are attached."}
    return result


def permit_metadata(permit: dict) -> dict:
    """Registry issuance is known; current legal effect and supersession are not.

    Different programs can coexist. Dates and numeric suffixes never establish
    supersession, and a successful parser never establishes current legal effect.
    """
    document = permit.get("document") or {}
    return {"registration": permit.get("registration"), "county": permit.get("county"),
            "county_fips": VA_COUNTY_FIPS.get(permit.get("county")),
            "program": permit.get("program"), "document_status": "issued",
            "issued_at": permit.get("issued_iso"), "effective_status": "unknown",
            "is_current_effective_limit": None, "latest_effective_status_verified": False,
            "supersession_status": "not_verified", "supersedes": [], "superseded_by": [],
            "parse_status": document.get("status", "not_fetched"),
            "legal_status_note": "Listed in the issued-permit registry. Current effectiveness, amendments and supersession have not been verified; programs may coexist."}


def condition_metadata(condition: dict) -> dict:
    """Apply safe condition scope even to previously retained extractions."""
    citation = condition.get("citation") or {}
    quote = citation.get("quote") or condition.get("quote") or ""
    metric, unit = condition.get("metric"), condition.get("unit")
    # A plural equipment group does not establish a per-generator allowance.
    # The exact subject and operational purpose remain in the quoted condition.
    if metric in {"generator_hours_limit", "mcrt_hours_limit"}:
        unit = "hours/year"
    if metric == "fuel_throughput_limit" and not re.search(
            r"per\s+year|annual|(?:12|twelve)[ -]month|365[ -]day", quote, re.I):
        unit = "gallons (period not verified)"
    return {"unit": unit, "scope": "quoted_condition", "applies_to_entire_site": False,
            "subject_scope": "Only the equipment and operating purpose stated in the quotation.",
            "is_document_condition": True, "is_permitted_limit": None,
            "is_current_effective_limit": None, "effective_status": "unknown"}


def _valid_coordinates(facility):
    lat, lon = facility.get("lat"), facility.get("lon")
    return (isinstance(lat, (float, int)) and not isinstance(lat, bool)
            and isinstance(lon, (float, int)) and not isinstance(lon, bool)
            and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)


def osm_coordinate_metadata(kind: str) -> dict:
    """Overpass area centers are bounding-box centers, not mapped site points."""
    if kind not in {"node", "way", "relation"}:
        return {}
    area = kind in {"way", "relation"}
    return {"coordinate_source": "osm_bounding_box_center" if area else "osm_node",
            "coordinate_accuracy_m": None, "coordinate_derivation_basis": "publisher_element_type",
            "coordinate_quality": {
                "collection_method": "Overpass bounding-box center" if area else "OSM community-mapped node",
                "reference_point": ("Center of element bounding box; may lie outside the mapped area" if area
                                    else "Community-mapped node point; physical location unverified"),
                "reported_accuracy": None}}


def _location_quality(facility):
    reasons, codes = [], []

    def flag(code, text):
        codes.append(code)
        reasons.append(text)

    valid = _valid_coordinates(facility)
    if not valid:
        flag("missing_or_invalid_coordinates", "Usable source coordinates are unavailable.")
    address = str(facility.get("address_line") or facility.get("address") or "")
    mailing = bool(_MAILBOX.search(address))
    if mailing:
        flag("mailing_address", "The reported address contains a post-office or private-mailbox identifier.")
    quality = facility.get("coordinate_quality") or {}
    coordinate_source = facility.get("coordinate_source")
    coordinate_basis = facility.get("coordinate_derivation_basis")
    match = re.fullmatch(r"osm:(node|way|relation):\d+", str(facility.get("id") or ""))
    id_kind = match[1] if match else None
    osm_kind = facility.get("osm_type") or id_kind
    if id_kind or facility.get("source_type") == "openstreetmap" or facility.get("osm_type"):
        if facility.get("osm_type") and id_kind and facility["osm_type"] != id_kind:
            flag("osm_element_type_conflict", "OSM element type conflicts with its source identifier; coordinate derivation requires review.")
        metadata = osm_coordinate_metadata(osm_kind)
        if metadata:
            # This also repairs display provenance when replaying older source
            # states without rewriting their bytes or original capture dates.
            coordinate_source = metadata["coordinate_source"]
            quality = {**quality, **metadata["coordinate_quality"]}
            coordinate_basis = coordinate_basis or ("inferred_from_retained_osm_type" if facility.get("osm_type")
                                                     else "inferred_from_retained_source_id")
        if osm_kind in {"way", "relation"}:
            flag("osm_bounding_box_center", "Overpass supplies an area bounding-box center, which may lie outside the mapped footprint. Map and grid assignment are withheld pending extent or location verification.")
        elif osm_kind != "node":
            flag("osm_coordinate_derivation_unknown", "OSM coordinate derivation is unknown; map and grid assignment require review.")
    method = str(quality.get("collection_method") or "")
    if "centroid" in method.lower():
        flag("geographic_centroid", "The publisher locates this record at a geographic centroid, not a verified facility point.")
    accuracy = facility.get("coordinate_accuracy_m")
    invalid_accuracy = accuracy is not None and (isinstance(accuracy, bool) or not isinstance(accuracy, (int, float))
                                                or not math.isfinite(accuracy) or accuracy < 0)
    if accuracy is None and quality.get("reported_accuracy") not in (None, ""):
        try:
            reported_accuracy = float(quality["reported_accuracy"])
            invalid_accuracy = (isinstance(quality["reported_accuracy"], bool)
                                or not math.isfinite(reported_accuracy) or reported_accuracy < 0)
        except (TypeError, ValueError):
            invalid_accuracy = True
    if invalid_accuracy:
        flag("invalid_coordinate_accuracy", "The supplied coordinate accuracy is invalid; location requires review.")
    elif isinstance(accuracy, (int, float)) and accuracy > MAX_SITE_PIN_ACCURACY_M:
        flag("coarse_coordinate_accuracy", "Publisher coordinate accuracy exceeds the 1 km site-pin review threshold.")
    if facility.get("coordinate_conflicts"):
        flag("source_coordinate_conflict", "Source programs report materially different coordinates for this record.")
    # Interpret derived attributes only for the coordinate source that produced
    # them. Never combine a fallback AIR coordinate with FRS's derived fields.
    raw = facility.get("combined_record") or {}
    selected_frs = facility.get("coordinate_source") == "frs" or (
        not facility.get("coordinate_source") and raw.get("FacLat") is not None)
    derived_fips = str(raw.get("FacDerivedStctyFIPS") or "") if selected_frs else ""
    derived_zip = str(raw.get("FacDerivedZip") or "") if selected_frs else ""
    reported_state = str(facility.get("state") or "").upper()
    reported_zip = str(facility.get("zip") or "")
    if re.fullmatch(r"\d{5}", derived_fips) and reported_state in STATE_FIPS and derived_fips[:2] != STATE_FIPS[reported_state]:
        flag("state_coordinate_conflict", "EPA's coordinate-derived state disagrees with the reported address state.")
    if re.match(r"^\d{5}", reported_zip) and re.match(r"^\d{5}", derived_zip) and reported_zip[:5] != derived_zip[:5]:
        flag("postal_coordinate_conflict", "EPA's coordinate-derived ZIP differs from the reported ZIP; location requires review.")
    suspect = bool(set(codes) - {"missing_or_invalid_coordinates"})
    return {"status": "suspect" if suspect else "unknown", "address_type": "mailing" if mailing else "unknown",
            "reasons": reasons, "reason_codes": codes, "map_eligible": valid and not codes,
            "physical_site_verified": False,
            "note": "Coordinates are publisher-reported location context, not an independently verified physical site.",
            "evidence": {"coordinate_source": coordinate_source,
                         "coordinate_derivation_basis": coordinate_basis,
                         "osm_element_type": osm_kind if id_kind or facility.get("source_type") == "openstreetmap" else None,
                         "collection_method": quality.get("collection_method"),
                         "reference_point": quality.get("reference_point"),
                         "reported_accuracy_m": accuracy, "reported_state": reported_state or None,
                         "coordinate_derived_county_fips": derived_fips or None,
                         "reported_zip": reported_zip or None, "coordinate_derived_zip": derived_zip or None,
                         "site_pin_accuracy_threshold_m": MAX_SITE_PIN_ACCURACY_M}}


def review_facilities(facilities: list[dict]) -> dict[str, dict]:
    """Return immutable review metadata keyed by source-record ID.

    Shared addresses/coordinates are candidates, never a merge or a campus
    count. A contact-location warning propagates across exact-coordinate peers,
    but ordinary colocated buildings remain separate and map-eligible.
    """
    output, address_groups, coordinate_groups, named_coordinates = {}, defaultdict(list), defaultdict(list), defaultdict(list)
    for facility in facilities:
        fid = facility["id"]
        if fid in output:
            raise ValueError("Duplicate source-record identity in location review: " + fid)
        output[fid] = {"location_quality": _location_quality(facility), "duplicate_candidates": [],
                       "site_identity": {"status": "ungrouped", "physical_site_id": None, "merge_verified": False}}
        address, city, state = (_key(facility.get("address_line") or facility.get("address")),
                                _key(facility.get("city")), _key(facility.get("state")))
        if address and city and state:
            address_groups[(address, city, state)].append(fid)
        if _valid_coordinates(facility):
            point = (facility["lat"], facility["lon"])
            coordinate_groups[point].append(fid)
            if name_key(facility.get("name")):
                named_coordinates[(name_key(facility["name"]), *point)].append(fid)

    for basis, groups in (("same_normalized_address_city_state", address_groups),
                          ("same_normalized_name_and_coordinates", named_coordinates)):
        for key, members in groups.items():
            if len(members) < 2:
                continue
            digest = hashlib.sha256(json.dumps([basis, key], separators=(",", ":")).encode()).hexdigest()[:16]
            group = {"id": "candidate:" + digest, "group_id": "candidate:" + digest,
                     "basis": basis, "record_ids": sorted(members),
                     "merge_verified": False, "note": "Possible shared site, campus, tenant records or duplicate registrations; distinct physical-site identity remains unresolved."}
            for fid in members:
                output[fid]["duplicate_candidates"].append(group)
                output[fid]["site_identity"]["status"] = "candidate_group"
    for members in coordinate_groups.values():
        if len(members) < 2:
            continue
        contacts = [fid for fid in members if set(output[fid]["location_quality"]["reason_codes"])
                    & {"mailing_address", "state_coordinate_conflict"}]
        if not contacts:
            continue
        for fid in members:
            quality = output[fid]["location_quality"]
            quality.update(status="suspect", map_eligible=False)
            quality["reason_codes"].append("shared_suspect_contact_location")
            quality["reasons"].append("These exact coordinates are shared with a mailing-address or state-conflicted record.")
            quality["evidence"]["shared_contact_record_ids"] = sorted(contacts)
    return output
