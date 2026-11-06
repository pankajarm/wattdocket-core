"""Read-only verification of every object in a pinned WattDocket data release.

Live:  python scripts/verify_production_release.py --expected-revision <git-sha>
Local: python scripts/verify_production_release.py --root-dir .production/public

Prints JSON; missing/failed required gates or a changing pointer exit nonzero.
This checks public data integrity, not browser journeys or original permit PDFs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import re
import ssl
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_facility_power import validate_facility_power
from verify_explain_registry import validate_explain_registry
from verify_permit_recovery import validate_permit_recovery, reviewed_identity_supported, REVIEWED_IDENTITY_METHOD

REQUIRED_SOURCES = {"echo": 400, "egrid": 25, "egrid_geometry": 25,
                    "eia930": 40, "ercot": 10, "virginia_permits": 150, "meta_actuals2024": 90, "massachusetts_energy": 14,
                    "ghgrp": 10, "disclosure_identity": 6, "google_water2025": 40,
                    "nyc_benchmarking": 161, "seattle_benchmarking": 22,
                    "google_disclosure_identity": 11, "meta_disclosure_identity": 8,
                    "municipal_disclosure_identity": 3,
                    "inl_c3_power": 47265, "nrel_esif_power": 5599,
                    "nebraska_facility_demand": 3, "nyc_facility_demand": 1}
KNOWN_SOURCES = {*REQUIRED_SOURCES, "texas_permits", "osm", "cornerstone_egrid2024", "google_water2025"}
OPTIONAL_REVIEW_SOURCES = {"virginia_site_registry": 13}
MAX_OBJECT_BYTES = 100_000_000
MAX_RELEASE_BYTES = 500_000_000


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def decode(raw):
    def bad_constant(value):
        raise ValueError("Nonfinite JSON number: " + value)
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=bad_constant)


def slug(value):
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def date(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp has no time zone: " + value)
    return parsed


class Checks:
    def __init__(self):
        self.count, self.failures, self.errors = 0, 0, []

    def require(self, condition, message):
        self.count += 1
        if not condition:
            self.failures += 1
            if len(self.errors) < 100:
                self.errors.append(message)
        return bool(condition)


def check_osm_coordinate_scope(fid, location, grid, claims, checks):
    """Independently infer point versus area from the public OSM identifier."""
    identity = re.fullmatch(r"osm:(node|way|relation):\d+", str(fid))
    if not identity:
        return
    kind = identity[1]
    evidence = location.get("evidence") or {}
    source = "osm_node" if kind == "node" else "osm_bounding_box_center"
    checks.require(evidence.get("osm_element_type") == kind and evidence.get("coordinate_source") == source,
                   "OSM coordinate provenance conflicts with its element identifier: " + fid)
    if kind in {"way", "relation"}:
        scenarios = [c for c in claims if str(c.get("id", "")).startswith("scenario-")
                     or c.get("scope") == "scenario" or c.get("range_kind") == "scenario"
                     or (c.get("inputs") or {}).get("input_source") == "illustrative_preset"]
        checks.require(location.get("map_eligible") is False and grid.get("subregion") is None
                       and grid.get("rate_lb_per_mwh") is None and not grid.get("alternative") and not scenarios,
                       "OSM area bounding-box center promoted to map/grid/scenario: " + fid)


class Reader:
    def __init__(self, root_dir, origin, timeout):
        self.root = root_dir.resolve() if root_dir else None
        self.origin, self.timeout = origin.rstrip("/"), timeout
        parsed = urllib.parse.urlparse(self.origin)
        if not self.root and (parsed.scheme != "https" or parsed.username or parsed.password or parsed.path not in ("", "/")):
            raise ValueError("Origin must be credential-free HTTPS without a path")
        try:
            import certifi
            self.context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self.context = ssl.create_default_context()

    def read(self, path, limit):
        if not path.startswith("/") or ".." in PurePosixPath(path).parts or "?" in path or "#" in path:
            raise ValueError("Unsafe release path: " + path)
        if self.root:
            target = (self.root / path.lstrip("/")).resolve()
            if not target.is_relative_to(self.root):
                raise ValueError("Release path escapes local root")
            with target.open("rb") as stream:
                raw = stream.read(limit + 1)
            metadata = {"path": path, "transport": "local_file"}
        else:
            request = urllib.request.Request(self.origin + path, headers={
                "Accept": "application/json", "Accept-Encoding": "gzip",
                "Cache-Control": "no-cache", "User-Agent": "WattDocket-release-verifier/1.0"})
            with urllib.request.urlopen(request, context=self.context, timeout=self.timeout) as response:
                if response.status != 200 or response.url != self.origin + path:
                    raise ValueError("Unexpected HTTP status/redirect: " + response.url)
                mime = response.headers.get_content_type()
                if mime != "application/json":
                    raise ValueError("JSON asset has wrong MIME type: " + mime)
                raw = response.read(limit + 1)
                metadata = {"path": path, "transport": "https", "status": response.status,
                            "content_type": mime, "content_encoding": response.headers.get("Content-Encoding"),
                            "etag": response.headers.get("ETag"), "cache_control": response.headers.get("Cache-Control"),
                            "age": response.headers.get("Age")}
        if len(raw) > limit:
            raise ValueError("Asset exceeds bounded byte limit: " + path)
        return raw, metadata

    def manifest(self, path):
        raw, metadata = self.read(path, 2_000_000)
        if raw.startswith(b"\x1f\x8b"):
            raw = gzip.GzipFile(fileobj=BytesIO(raw)).read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Manifest exceeds decompressed limit")
        return raw, decode(raw), metadata


def fetch_object(reader, base, name, expected):
    if name.startswith("/") or ".." in PurePosixPath(name).parts or not name.endswith(".json"):
        raise ValueError("Unsafe or unknown object name: " + name)
    size, compressed_size = expected["bytes"], expected["gzip_bytes"]
    if not isinstance(size, int) or not 0 < size <= MAX_OBJECT_BYTES or not isinstance(compressed_size, int) or not 0 < compressed_size <= MAX_OBJECT_BYTES:
        raise ValueError("Invalid manifest byte count: " + name)
    if not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]):
        raise ValueError("Invalid object digest: " + name)
    raw, metadata = reader.read(base + "/" + name, compressed_size)
    if len(raw) != compressed_size or not raw.startswith(b"\x1f\x8b"):
        raise ValueError("Expected manifest gzip byte count and gzip representation: " + name)
    content = gzip.GzipFile(fileobj=BytesIO(raw)).read(size + 1)
    if len(content) != size or sha(content) != expected["sha256"]:
        raise ValueError("Decompressed SHA-256 or byte count mismatch: " + name)
    return decode(content), content, {**metadata, "sha256": sha(content), "bytes": len(content),
                                      "gzip_bytes": len(raw), "verified": True}


def check_municipal_source_cells(location, checks):
    """Literal publisher checks independent of the collectors/publication gate.

    This compares the released values with the retained native row. It does
    not claim to re-download the publisher or authenticate private raw bytes.
    """
    sid = location.get("source_id")
    if sid not in {"nyc_benchmarking", "seattle_benchmarking"}:
        return
    key = location.get("disclosure_location_id")
    native = location.get("raw_source_row")
    if not checks.require(isinstance(native, dict), "Municipal native row missing: " + str(key)):
        return
    nyc = sid == "nyc_benchmarking"
    fields = ({"electricity_consumption": ("electricity_use_grid_purchase_2", "kWh"),
               "water_use_reported": ("water_use_all_water_sources", "kgal"),
               "reported_ghg_total": ("total_location_based_ghg", "metric tons CO2e")} if nyc else
              {"electricity_consumption": ("electricity_kwh", "kWh"),
               "reported_ghg_total": ("totalghgemissions", "metric tons CO2e")})
    id_field, year_field, count_field = (("property_id", "report_year", "number_of_buildings") if nyc else
                                        ("osebuildingid", "datayear", "numberofbuildings"))
    identity = str(native.get(id_field, ""))
    valid_id = bool(re.fullmatch(r"[0-9]+", identity)) and int(identity) > 0
    prefix = "nyc-ll84:" if nyc else "seattle-benchmarking:"
    checks.require(valid_id and key == prefix + str(int(identity)), "Municipal identity differs from native property: " + str(key))
    raw_year = str(native.get(year_field, ""))
    valid_year = bool(re.fullmatch(r"[0-9]{4}", raw_year)) and 2010 <= int(raw_year) < datetime.now(timezone.utc).year
    checks.require(valid_year and location.get("latest_dataset_year") == int(raw_year), "Municipal latest complete year differs from native row: " + str(key))
    count = str(native.get(count_field, ""))
    valid_count = bool(re.fullmatch(r"[0-9]+", count)) and 1 <= int(count) <= 1000
    if valid_count:
        checks.require(location.get("number_of_buildings") == int(count) and
                       location.get("reporting_scope") == ("building" if int(count) == 1 else "campus"),
                       "Municipal building count/boundary changed: " + str(key))
    else:
        checks.require(False, "Municipal native building count invalid: " + str(key))
    address = location.get("address") or {}
    checks.require(address.get("street") == native.get("address_1" if nyc else "address") and
                   address.get("city") == native.get("city") and address.get("state") == ("NY" if nyc else "WA"),
                   "Municipal publisher address changed: " + str(key))
    endpoint = urllib.parse.urlparse(location.get("source_url") or "")
    checks.require(endpoint.scheme == "https" and endpoint.hostname == ("data.cityofnewyork.us" if nyc else "data.seattle.gov")
                   and endpoint.path == ("/resource/5zyy-y8am.json" if nyc else "/resource/teqw-tu6e.json"),
                   "Municipal source is not its reviewed publisher dataset: " + str(key))
    source_ids = location.get("source_identifiers") or {}
    checks.require(source_ids.get("nyc_property_id" if nyc else "seattle_ose_building_id") == identity,
                   "Municipal native property identifier lost: " + str(key))
    if nyc:
        energy_flags = ("alert_energy_meter_has_less", "alert_energy_meter_has_gaps", "alert_energy_meter_has",
                        "alert_energy_no_meters", "alert_energy_meter_has_single")
        checks.require("Data Center" in [native.get(f) for f in
                       ("primary_property_type_self", "primary_property_type", "largest_property_use_type")],
                       "NYC property falls outside reviewed classification: " + str(key))
        checks.require(str(native.get("year_ending", ""))[:10] == raw_year + "-12-31",
                       "NYC native annual period differs: " + str(key))
        energy_ok = native.get("construction_status") == "Existing" and native.get("metered_areas_energy") == "Whole Property" and all(native.get(f) == "Ok" for f in energy_flags)
        water_ok = native.get("construction_status") == "Existing" and native.get("metered_areas_water") == "Whole Property" and native.get("alert_water_meter_has_less") == "Ok"
        native_quality = (*energy_flags, "metered_areas_energy", "metered_areas_water", "estimated_values_energy",
                          "estimated_values_water", "alert_water_meter_has_less", "construction_status",
                          "alert_data_center_issue_with", "data_center_energy_estimates")
    else:
        checks.require(native.get("state") == "WA" and "Data Center" in
                       [native.get("epapropertytype"), native.get("largestpropertyusetype")],
                       "Seattle property falls outside reviewed state/classification: " + str(key))
        energy_ok = native.get("compliancestatus") == "Compliant" and native.get("complianceissue") == "No Issue" and native.get("demolished") is False
        water_ok = False
        native_quality = ("compliancestatus", "complianceissue", "demolished")
    quality = location.get("source_quality") or {}
    checks.require(all(quality.get(f) == native.get(f) for f in native_quality),
                   "Municipal displayed quality differs from native flags: " + str(key))
    if nyc:
        checks.require(quality.get("energy_publication_status") == ("source_qualified" if energy_ok else "withheld_incomplete_or_unknown") and
                       quality.get("water_publication_status") == ("source_qualified" if water_ok else "withheld_incomplete_or_unknown"),
                       "NYC displayed completeness status misstates native flags: " + str(key))
    expected = {}
    missing = {None, "", "Not Available", "Not Applicable", "NA", "N/A"}
    for metric, (field, _) in fields.items():
        qualified = water_ok if metric == "water_use_reported" else energy_ok
        cell = native.get(field)
        if not qualified or (isinstance(cell, (str, int, float, type(None))) and cell in missing):
            continue
        if checks.require(not isinstance(cell, bool) and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(cell))),
                          "Municipal qualified native quantity malformed: " + str(key) + "/" + metric):
            expected[metric] = Decimal(str(cell))
    rows = location.get("observations", [])
    observed_metrics = [r.get("metric") for r in rows]
    checks.require(len(observed_metrics) == len(set(observed_metrics)) and set(observed_metrics) == set(expected),
                   "Municipal observations omit qualified values or publish flagged/missing values: " + str(key))
    missing_metrics = location.get("missing_metrics", [])
    checks.require(set(fields) - set(expected) <= set(missing_metrics) and not set(expected) & set(missing_metrics)
                   and (nyc or "water_use_reported" in missing_metrics),
                   "Municipal missing-value labels differ from native evidence: " + str(key))
    for row in rows:
        metric = row.get("metric")
        if metric not in fields:
            checks.require(False, "Municipal metric not defined by publisher: " + str(key))
            continue
        field, unit = fields[metric]
        checks.require(metric in expected and numeric(row.get("value")) and Decimal(str(row["value"])) == expected[metric],
                       "Municipal published quantity differs from native cell: " + str(key) + "/" + metric)
        checks.require(row.get("source_field") == field and row.get("unit") == unit,
                       "Municipal field/unit changed: " + str(key) + "/" + metric)
        checks.require(valid_year and row.get("data_year") == int(raw_year) and
                       row.get("period_start") == raw_year + "-01-01T00:00:00Z" and
                       row.get("period_end") == str(int(raw_year) + 1) + "-01-01T00:00:00Z" and
                       row.get("observation_id") == f"{key}:{raw_year}:{metric}",
                       "Municipal observation identity/period differs from native row: " + str(key))
        checks.require(row.get("source_id") == sid and row.get("source_url") == location.get("source_url") and
                       row.get("source_sha256") == location.get("source_sha256") and row.get("source_quality") == quality,
                       "Municipal observation lost its native row source/quality: " + str(key))
        estimate_field = "estimated_values_water" if metric == "water_use_reported" else "estimated_values_energy"
        basis = ("publisher_calculated_from_reported_energy" if metric == "reported_ghg_total" else
                 "reported_annual_usage_includes_estimates" if nyc and native.get(estimate_field) == "Yes" else "reported_annual_usage")
        checks.require(row.get("reporting_basis") == basis and row.get("is_meter_observation") is False and
                       row.get("metered_share_known") is False,
                       "Municipal reported/calculated/estimated quantity promoted to meter measurement: " + str(key))


def validate_disclosures(objects, coverage, checks):
    """Independently verify emitted reporting entities and non-allocation."""
    payload = objects.get("facility-observations.json") or {}
    locations = payload.get("locations", [])
    checks.require(bool(locations) and payload.get("schema_version") == "1", "Reported disclosure payload missing")
    ids, observations, linked = set(), {}, set()
    counts = {"electricity": 0, "water": 0, "emissions": 0}
    verified_links = 0
    actual = {"power": set(), "water": set(), "carbon": set()}
    metrics = {"electricity_consumption", "water_withdrawal", "water_use_reported", "water_discharge", "water_consumption", "emissions_scope1", "emissions_scope2_location", "emissions_scope2_market", "emissions_scope1_and2_market", "reported_ghg_total"}
    by_id = {}
    for location in locations:
        check_municipal_source_cells(location, checks)
        key = location.get("disclosure_location_id")
        checks.require(key not in ids and isinstance(key, str), "Repeated reporting entity")
        ids.add(key); by_id[key] = location
        families = set()
        for row in location.get("observations", []):
            oid = row.get("observation_id"); metric = row.get("metric")
            checks.require(oid not in observations, "Repeated reported observation")
            observations[oid] = row
            family = "electricity" if metric == "electricity_consumption" else "water" if str(metric).startswith("water_") else "emissions"
            families.add(family)
            checks.require(metric in metrics and row.get("disclosure_location_id") == key, "Unknown metric or wrong reporting entity")
            checks.require(numeric(row.get("value")) and row["value"] >= 0, "Missing or invalid reported quantity")
            checks.require(row.get("reporting_scope") == location.get("reporting_scope") in {"campus", "building", "regulatory_facility", "operator_location"}, "Reporting boundary changed")
            year = row.get("data_year")
            checks.require(isinstance(year, int) and not isinstance(year, bool) and 1990 <= year <= datetime.now(timezone.utc).year, "Invalid disclosure year")
            checks.require(row.get("period_start") == f"{year}-01-01T00:00:00Z" and row.get("period_end") == f"{year + 1}-01-01T00:00:00Z", "Disclosure annual period mismatch")
            checks.require(bool(row.get("reporting_basis")) and bool(row.get("methodology_note")) and bool(row.get("unit")), "Disclosure method/unit missing")
            checks.require(family != "electricity" or row["unit"].lower() in {"mwh", "kwh", "gwh", "kbtu"}, "Annual energy presented as demand")
            checks.require(str(row.get("source_url", "")).startswith("https://") and bool(row.get("publisher")) and bool(re.fullmatch("[a-f0-9]{64}", row.get("source_sha256", ""))), "Disclosure source attribution/hash missing")
            receipt = row.get("source_receipt") or {}
            checks.require(receipt.get("url") == row.get("source_url") and receipt.get("sha256") == row.get("source_sha256") and receipt.get("raw_key") == "raw/" + row["source_sha256"] + ".gz" and receipt.get("retrieved_at") == row.get("retrieved_at") and bool(row.get("retrieved_at")), "Disclosure original capture receipt differs from value attribution")
            checks.require(bool(row.get("source_page") or row.get("source_field") or row.get("source_quote")), "Disclosure source locator missing")
            checks.require(row.get("facility_binding_verified") is False and row.get("inventory_allocation") is False and row.get("value_origin") == "reported_disclosure", "Raw observation allocated to inventory")
            if location.get("source_id") in {"massachusetts_energy", "nyc_benchmarking", "seattle_benchmarking"} and metric == "reported_ghg_total":
                checks.require(row.get("reporting_basis") == "publisher_calculated_from_reported_energy", "Calculated state GHG presented as measured")
        for family in families: counts[family] += 1
        for link in location.get("record_links", []):
            checks.require(link.get("disclosure_location_id") == key, "Wrong disclosure link entity")
            if link.get("verification_status") != "verified":
                checks.require(link.get("inventory_allocation") is False, "Candidate link allocated")
                continue
            verified_links += 1
            linked.add(link["record_id"])
            checks.require(link.get("evidence") and all(e.get("verified") and e.get("content_sha256") == (e.get("receipt") or {}).get("sha256") for e in link.get("evidence", [])), "Verified link lacks captured primary evidence")
            if link.get("inventory_allocation"):
                checks.require(link.get("relationship") == "same_physical_scope" and link.get("allocation_allowed") is True and link.get("same_physical_scope_verified") is True, "Unsupported boundary allocation")
                for family in families: actual[{"electricity":"power", "water":"water", "emissions":"carbon"}[family]].add(link["record_id"])
            else:
                checks.require(link.get("relationship") == "within_disclosure_location", "Unknown verified relationship")
    checks.require(len(locations) == coverage.get("disclosure_locations") and len(observations) == payload.get("observation_count") == coverage.get("disclosure_observations"), "Disclosure counts disagree")
    checks.require(payload.get("coverage") == {k: coverage[k] for k in payload.get("coverage", {})}, "Disclosure coverage differs from manifest")
    checks.require(len(linked) == coverage.get("records_with_linked_disclosures") and verified_links == coverage.get("reviewed_disclosure_links"), "Disclosure join counts disagree")
    for family, count in counts.items(): checks.require(count == coverage.get("reported_" + family + "_locations"), "Disclosure family count differs: " + family)
    for family, records in actual.items(): checks.require(len(records) == coverage.get("actual_" + family + "_records"), "Same-boundary coverage differs: " + family)
    card_linked = set()
    for card in objects["cards.json"].values():
        fid = card["facility"]["id"]
        groups = card.get("disclosures", [])
        checks.require(objects["operators.json"][slug(fid)].get("disclosures", []) == groups, "Card/operator disclosure mismatch")
        for group in groups:
            location, link = group["location"], group["link"]
            checks.require(location == by_id.get(location.get("disclosure_location_id")) and link in location.get("record_links", []), "Card disclosure differs from reporting entity")
            checks.require(link.get("record_id") == fid and link.get("verification_status") == "verified", "Card disclosure linked to wrong or unverified record")
            card_linked.add(fid)
    checks.require(card_linked == linked, "Reporting-entity/card links differ")


def validate_data(objects, raw_objects, manifest, checks):
    release, coverage = manifest["release_id"], manifest["coverage"]
    status, inventory = objects["data-status.json"], objects["facilities.json"]
    cards, drawers, operators = (objects[name] for name in ("cards.json", "drawers.json", "operators.json"))
    checks.require(status["coverage"] == coverage and status["built_at"] == manifest["built_at"], "Manifest/status coverage or time differs")
    for name, obj in objects.items():
        if isinstance(obj, dict) and "release_id" in obj:
            checks.require(obj["release_id"] == release, name + " has another release_id")
    entries = inventory["results"]
    ids = [f["id"] for f in entries]
    keys = {slug(fid) for fid in ids}
    checks.require(inventory.get("truncated") is False, "Facility inventory truncated or unspecified")
    checks.require(len(set(ids)) == len(keys) == len(ids), "Duplicate IDs or colliding slugs")
    checks.require(set(cards) == set(drawers) == set(operators) == keys, "Card/drawer/operator/index identity sets differ")
    checks.require(coverage["facility_records"] == len(entries), "Facility count mismatch")
    checks.require(coverage["regulator_verified"] == sum(bool(f["class_verified"]) for f in entries), "Verified-facility count mismatch")
    checks.require(coverage["with_coordinates"] == sum(f.get("lat") is not None and f.get("lon") is not None for f in entries), "Coordinate count mismatch")
    checks.require(coverage["with_grid_binding"] == sum(c["grid"].get("subregion") is not None for c in cards.values()), "Grid-binding count mismatch")
    checks.require(coverage["map_eligible_records"] == sum(f["location_quality"]["map_eligible"] for f in entries), "Eligible map-point count mismatch")
    checks.require(coverage["suspect_location_records"] == sum(f["location_quality"]["status"] == "suspect" for f in entries), "Suspect-location count mismatch")
    validate_disclosures(objects, coverage, checks)
    sources = status["sources"]
    source_map = {s["id"]: s for s in sources}
    expected_sources = KNOWN_SOURCES | (set(source_map) & set(OPTIONAL_REVIEW_SOURCES))
    checks.require(len(source_map) == len(sources) and set(source_map) == expected_sources, "Missing, duplicate or unknown source IDs")
    if any(row.get("site_identity_review") for row in status.get("permit_recovery", {}).get("documents", [])):
        checks.require("virginia_site_registry" in source_map, "Reviewed regulator sites lack their collection source")
    checks.require(inventory["source_status"] == sources, "Index/source-status snapshots differ")
    for sid, minimum in {**REQUIRED_SOURCES, **{k: v for k, v in OPTIONAL_REVIEW_SOURCES.items() if k in source_map}}.items():
        source = source_map.get(sid, {})
        checks.require(source.get("status") in {"current", "stale"} and source.get("record_count", 0) >= minimum,
                       "Required source missing/below coverage gate: " + sid)
        checks.require(bool(re.fullmatch(r"[0-9a-f]{64}", source.get("result_sha256", ""))), "Required source lacks hash: " + sid)
        observed = source.get("observation_end") if sid in {"eia930", "ercot"} else None
        timestamps = [source.get("last_success_at"), observed] if observed else [source.get("last_success_at")]
        for timestamp in timestamps:
            try:
                age = (datetime.now(timezone.utc) - date(timestamp)).total_seconds()
                checks.require(-3600 <= age <= source["stale_after_seconds"] * 2, sid + " has future/expired observation or capture: " + str(timestamp))
            except (AttributeError, TypeError, ValueError, KeyError):
                checks.require(False, sid + " lacks valid freshness timestamp/policy")
    for source_id in ("echo", "osm"):
        checks.require(sum(f["source"] == source_id and f.get("source_lane", source_id) == source_id for f in entries) == source_map.get(source_id, {}).get("record_count", 0),
                       source_id + " inventory count differs from source")
    checks.require(coverage["virginia_permit_documents"] == source_map["virginia_permits"]["record_count"], "VA registry count mismatch")
    checks.require(coverage["texas_permit_documents"] == source_map["texas_permits"]["record_count"], "Texas selected-document count mismatch")
    checks.require(coverage["permit_documents"] == coverage["virginia_permit_documents"] + coverage["texas_permit_documents"], "Total permit count mismatch")
    checks.require(coverage["permit_documents_parsed"] == coverage["virginia_permit_documents_parsed"] + coverage["texas_permit_documents_parsed"], "Readable-document count mismatch")
    validate_permit_recovery(objects, coverage, checks)

    scenario_count = archived_limits = stale_limits = document_links = 0
    for entry in entries:
        fid, key = entry["id"], slug(entry["id"])
        checks.require(fid.startswith(entry["source"] + ":") and entry["source"] in {"echo", "osm"}, "Source namespace mismatch: " + fid)
        if entry.get("registry_id") is not None:
            checks.require(bool(re.fullmatch(r"\d{12}", str(entry["registry_id"]))), "Program ID falsely labeled FRS: " + fid)
        card, operator, group = cards[key], operators[key], drawers[key]
        checks.require(card["facility"]["id"] == operator["facility_id"] == fid, "Record identity mismatch: " + fid)
        checks.require(card["release_id"] == operator["release_id"] == release, "Record release mismatch: " + fid)
        checks.require(card["data_status"] == operator["data_status"] and card["data_status"]["built_at"] == status["built_at"], "Record status snapshot mismatch: " + fid)
        checks.require(all(s == source_map.get(s["id"]) for s in card["data_status"]["sources"]), "Card source dates differ from release: " + fid)
        facility = card["facility"]
        supporting = {entry.get("source_lane") or entry["source"]}
        checks.require(facility.get("source_lane", facility["source"]) == entry.get("source_lane", entry["source"]), "Card/index source lane mismatch: " + fid)
        if card.get("disclosures"):
            supporting.update(group["link"].get("identity_source_id", "disclosure_identity") for group in card["disclosures"])
            supporting.update(group["location"]["source_id"] for group in card["disclosures"])
        if facility["class_verified"] or facility.get("identity_candidates"):
            supporting.add("virginia_permits")
        if any(row.get("site_identity_review") for row in facility.get("permit_reviews", [])):
            supporting.add("virginia_site_registry")
        if facility.get("documents"):
            supporting.add("texas_permits")
        if card["grid"].get("subregion"):
            supporting |= {"egrid", "egrid_geometry"}
        if card["grid"].get("alternative"):
            supporting.add("cornerstone_egrid2024")
        checks.require({s["id"] for s in card["data_status"]["sources"]} == supporting, "Unrelated or missing card evidence sources: " + fid)
        checks.require(entry.get("site_id") is None and facility.get("site_id") is None and facility["site_identity"]["merge_verified"] is False, "Source record presented as verified physical-site grouping: " + fid)
        checks.require(facility["location_quality"] == entry["location_quality"], "Index/card location review differs: " + fid)
        checks.require(facility["location_quality"]["physical_site_verified"] is False, "Unimplemented physical-site verification claimed: " + fid)
        check_osm_coordinate_scope(fid, facility["location_quality"], card["grid"], card["claims"], checks)
        if not facility["location_quality"]["map_eligible"]:
            checks.require(card["grid"].get("subregion") is None, "Withheld physical location still has regional assignment: " + fid)
        identity = facility["identity_match"]
        if facility["class_verified"]:
            cross_reference = identity.get("method") == REVIEWED_IDENTITY_METHOD and any(reviewed_identity_supported(facility, row) for row in facility.get("permit_reviews", []))
            checks.require((identity.get("method") == "county_and_registration" or cross_reference) and identity.get("status") == "matched", "Name-only identity falsely verified: " + fid)
            matched_ids = identity.get("matched_source_ids", [])
            air_values = " ".join(str(r.get("value", "")) for r in facility["record_summary"] if r["label"] == "AIR program ID")
            checks.require(bool(matched_ids) and all(i in air_values for i in matched_ids), "Verified identity is not supported by published AIR IDs: " + fid)
        else:
            checks.require(not facility["permit_limits"] and all(c["value"] == "NOT_PUBLISHED" for c in card["claims"] if c["id"] == "diesel-mw"), "Unverified name match attached permit facts: " + fid)
        checks.require(card["hourly"] is None and operator["synthetic_meter_data"] is False, "Fabricated facility hourly measurements: " + fid)
        claims = {c["id"]: c for c in card["claims"]}
        checks.require(len(claims) == len(card["claims"]) and set(group) == {slug(k) for k in claims}, "Claim/drawer IDs differ: " + fid)
        for cid in ("power-mw", "facility-carbon", "water-gal"):
            checks.require(claims[cid]["value"] == "NOT_PUBLISHED" and claims[cid]["as_of"] is None, "Manufactured actual measurement: " + fid + "/" + cid)
        for cid, claim in claims.items():
            drawer = group[slug(cid)]
            checks.require(drawer["claim_id"] == cid and drawer["scope"] == claim["scope"], "Drawer claim/scope mismatch: " + fid + "/" + cid)
            if numeric(claim["value"]):
                checks.require(bool(claim["citations"] or claim["calc_id"]), "Numeric claim lacks evidence/calculation: " + fid + "/" + cid)
            for citation in claim["citations"]:
                checks.require(citation.get("verified") is True and bool(citation.get("quote")) and bool(re.fullmatch(r"[0-9a-f]{64}", citation.get("doc_sha256", ""))), "Unverified/malformed claim citation: " + fid + "/" + cid)
                url = citation.get("url", "")
                if url.startswith(manifest["base_path"] + "/"):
                    path = url[len(manifest["base_path"]) + 1:]
                    content = raw_objects.get(path, b"")
                    checks.require(path in raw_objects and sha(content) == citation["doc_sha256"] and content.decode().strip() == citation["quote"], "Public citation differs from evidence bytes: " + fid + "/" + cid)
        carbon = claims.get("scenario-carbon")
        if card["grid"].get("subregion") is None:
            checks.require(carbon is None and "scenario-power" not in claims, "Unbound point has invented scenario: " + fid)
        else:
            scenario_count += 1
            checks.require(carbon is not None, "Bound point lacks scenario: " + fid)
            if carbon:
                checks.require(carbon["scope"] == carbon["range_kind"] == "scenario" and carbon["badge"] == "MODELED", "Scenario presented as reported: " + fid)
                checks.require(carbon["as_of"] == card["grid"]["data_vintage"] + "-12-31", "Factor vintage replaced by retrieval date: " + fid)
                rate = card["grid"]["rate_lb_per_mwh"]
                checks.require(numeric(rate), "Scenario grid rate unavailable: " + fid)
                if numeric(rate):
                    checks.require(set(carbon["range"]) == {"low", "base", "high"}, "Scenario uses percentile labels: " + fid)
                    checks.require(carbon["inputs"]["facility_measurement"] is False and carbon["inputs"]["factor_year"] == int(card["grid"]["data_vintage"]), "Scenario input scope/year missing: " + fid)
                    for bound, utilization in (("low", .45), ("base", .65), ("high", .80)):
                        expected = 10 * 8760 * utilization * rate / 2204.622621848776
                        actual = carbon["range"].get(bound)
                        checks.require(numeric(actual) and math.isclose(actual, expected, abs_tol=.1), "Scenario arithmetic mismatch: " + fid + "/" + bound)
                    checks.require(carbon["value"] == carbon["range"]["base"], "Scenario central estimate differs: " + fid)
        diesel = claims["diesel-mw"]
        if numeric(diesel["value"]):
            checks.require(diesel.get("evidence_scope") == "equipment_table_page" and diesel.get("is_complete_fleet") is False, "Table total promoted to campus capacity: " + fid)
            checks.require(bool(diesel["citations"]) and diesel.get("as_of") is not None, "Generator table lacks date/receipts: " + fid)
        methods = {m["key"]: m for m in operator["methods"]}
        checks.require(methods["market"]["tonnes"] is None and methods["hourly"]["tonnes"] is None, "Missing accounting emissions encoded as zero: " + fid)
        checks.require(methods["location"]["tonnes"] == (carbon["value"] if carbon else None), "Operator scenario disagrees or manufactures zero: " + fid)
        checks.require(operator["matched_pct"] is None and operator["peak"]["pct"] is None and all(c["pct"] is None for c in operator["compliance"]), "Missing percentages encoded as zero: " + fid)
        for limit in card["facility"].get("permit_limits", []):
            checks.require(limit.get("scope") == "quoted_condition" and limit.get("applies_to_entire_site") is False, "Permit condition lost scope: " + fid)
            checks.require(limit.get("effective_status") == "unknown" and limit.get("latest_effective_status_verified") is False, "Unresolved permit condition represented as current legal obligation: " + fid)
            # Independently compare published registration/county to public AIR
            # identifiers; do not merely trust the builder's 'matched' label.
            identities = [re.fullmatch(r"VA00000051(\d{3})(\d{5})", value) for value in identity.get("matched_source_ids", [])]
            direct_id = any(m and m.group(1) == limit.get("county_fips") and m.group(2) == limit["registration"].split("-")[0] for m in identities)
            reviewed_id = identity.get("method") == REVIEWED_IDENTITY_METHOD and any(
                row.get("registration") == limit.get("registration") and row.get("content_sha256") == limit.get("content_sha256")
                and reviewed_identity_supported(facility, row) for row in facility.get("permit_reviews", []))
            checks.require(direct_id or reviewed_id, "Permit county/registration conflicts with record AIR identity: " + fid)
            checks.require(limit.get("representation") in {"reader_text", "publisher_pdf", "reviewed_pdf_transcription"} and bool(limit.get("page_derivation")), "Permit lacks representation/page provenance: " + fid)
            if limit.get("freshness") == "archived":
                archived_limits += 1
                checks.require(limit.get("retrieved_at") is None and bool(limit.get("archived_by_at")), "Archive date presented as retrieval: " + fid)
            if limit.get("freshness") == "stale":
                stale_limits += 1
                checks.require(limit.get("latest_fetch_status") == "failed" and bool(limit.get("latest_fetch_error")), "Stale permit lacks failure disclosure: " + fid)
        for document in card["facility"].get("documents", []):
            document_links += 1
            checks.require(document.get("is_current_effective_limit") is False and document.get("latest_effective_status_verified") is False, "Selected Texas document falsely current/effective: " + fid)
            for statement in document.get("statements", []):
                checks.require(statement.get("scope") == "document_statement" and statement.get("verified") is True and "value" not in statement, "Texas quote promoted to unsupported numeric fact: " + fid)

    regions = objects["grid/index.json"]["regions"]
    codes = [r["code"] for r in regions]
    checks.require(len(set(codes)) == len(codes) == coverage["grid_authorities"], "Grid authority counts/IDs disagree")
    reported_intervals = 0
    for item in regions:
        region = objects["grid/" + item["code"].lower() + ".json"]
        checks.require(region["code"] == item["code"] and region["source_id"] == "eia930" and region["unit"] == "MW", "Grid scope/source/unit mismatch: " + item["code"])
        readings = region["readings"]
        reported_intervals += region["reported_readings_count"]
        times = [r["interval_start_utc"] for r in readings]
        checks.require(times == sorted(set(times)) and len(readings) == item["readings_count"], "Grid intervals duplicate/unordered/count mismatch: " + item["code"])
        checks.require(region.get("intensity_available") is False and all(r.get("intensity_lb_co2e_per_mwh") is None for r in readings), "Unsupported hourly carbon: " + item["code"])
    checks.require(reported_intervals == source_map["eia930"]["record_count"], "EIA reported interval count differs from source")
    telemetry = objects["grid/ercot-five-minute.json"]
    checks.require(telemetry["source_id"] == "ercot" and telemetry["unit"] == "MW" and telemetry["interval_minutes"] == 5, "ERCOT scope/units/source mismatch")
    checks.require(telemetry["readings_count"] == len(telemetry["readings"]) and all(r["demand_mw"] is None and r.get("intensity_lb_co2e_per_mwh") is None for r in telemetry["readings"]), "ERCOT fuel generation manufactures demand/carbon")
    checks.require(objects.get("grid/erco.json", {}).get("five_minute") == telemetry, "ERCOT embedded/standalone snapshots differ")
    fallback = objects["resolve-fallback.json"]
    checks.require(fallback["range_kind"] == "scenario" and fallback["reference_mw"] == 10 and fallback["state_default"] == {}, "Address fallback mislabels scenario/guesses state")
    expected_paths = {"facilities.json", "cards.json", "drawers.json", "operators.json", "data-status.json",
                      "grid/index.json", "egrid-subregions.json", "resolve-fallback.json", "grid/ercot-five-minute.json"}
    expected_paths |= {"grid/" + code.lower() + ".json" for code in codes}
    expected_paths |= {"evidence/egrid/" + code + ".json" for code in fallback["subregions"]}
    expected_paths |= validate_facility_power(objects, coverage, checks)
    expected_paths |= validate_explain_registry(objects, raw_objects, manifest, checks)
    if "facility-observations.json" in objects:
        expected_paths.add("facility-observations.json")
    if "operator-water.json" in objects:
        water = objects["operator-water.json"]
        expected_paths.add("operator-water.json")
        checks.require(water["scope"] == "operator_reported_location" and water["facility_binding_verified"] is False, "Operator water disclosure falsely joined to facilities")
        checks.require(len(water["observations"]) == coverage["operator_location_water_records"] == source_map["google_water2025"]["record_count"], "Operator location water count mismatch")
        for row in water["observations"]:
            checks.require(row["data_year"] == 2025 and row["unit"] == "million gallons" and row["facility_binding_verified"] is False, "Water year/unit/facility scope changed")
            checks.require(row["source_page"] == 97 and re.fullmatch(r"[0-9a-f]{64}", row["pdf_sha256"]) is not None, "Water disclosure lacks a PDF receipt")
            checks.require("may include estimates" in row["measurement_basis"], "Operator water measurement qualification omitted")
            withdrawal, discharge, consumption = (row[k + "_million_gallons"] for k in ("withdrawal", "discharge", "consumption"))
            checks.require(all(numeric(v) and v >= 0 for v in (withdrawal, discharge, consumption)) and abs(withdrawal - discharge - consumption) <= .151, "Water disclosure quantities fail mass balance")
        aggregates = {row["location_label"]: row for row in water["aggregates"]}
        checks.require(set(aggregates) == {"Other data center locations", "Data centers total"} and len(water["aggregates"]) == 2, "Water aggregates are missing, duplicated or changed")
        if set(aggregates) == {"Other data center locations", "Data centers total"}:
            for row in aggregates.values():
                values = [row[k + "_million_gallons"] for k in ("withdrawal", "discharge", "consumption")]
                tolerance = 1.5 if row["location_label"] == "Data centers total" else .151
                checks.require(row["reporting_scope"] == "operator aggregate" and row["facility_binding_verified"] is False and all(numeric(v) and v >= 0 for v in values) and abs(values[0] - values[1] - values[2]) <= tolerance, "Water aggregate scope or mass balance is invalid")
            for metric in ("withdrawal", "discharge", "consumption"):
                field = metric + "_million_gallons"
                total = sum(row[field] for row in water["observations"]) + aggregates["Other data center locations"][field]
                checks.require(abs(total - aggregates["Data centers total"][field]) <= 2.55, "Named locations plus Other do not reconcile to water total: " + metric)
    if "grid/annual-comparison.json" in objects:
        comparison = objects["grid/annual-comparison.json"]
        checks.require(comparison["scope"] == "regional_comparison_only" and comparison["geography_binding_verified"] is False, "Alternative comparison claims verified geography")
        checks.require(comparison["validation"]["status"] == "validated_with_disclosed_method_difference", "Alternative method is unvalidated")
        validation = comparison["validation"]
        checks.require(validation["declared_gwp"] == validation["selected_comparison_gwp"] == "IPCC AR5", "Alternative declared/selected method mislabeled")
        checks.require(validation["comparison_gwp"] == "IPCC AR6 non-fossil methane" and validation["comparison_gwp_factors"] == {"co2": 1, "ch4": 27, "n2o": 273}, "Published companion comparison GWP disclosure is wrong")
        checks.require(validation["published_annual_mass_gwp"]["basis"] == "IPCC AR6" and validation["published_annual_mass_gwp"]["factors"] == {"co2": 1, "ch4": 29.8, "n2o": 273}, "Published annual-mass GWP disclosure is wrong")
        checks.require(len(comparison["regions"]) == 27, "Alternative lacks all 27 named regions")
        expected_paths.add("grid/annual-comparison.json")
        for code, row in comparison["regions"].items():
            path = "evidence/cornerstone-egrid2024/" + code + ".json"
            expected_paths.add(path)
            checks.require(objects.get(path) == row and row["data_year"] == 2024 and row["gwp_basis"] == "IPCC AR5", "Alternative evidence/year/method mismatch: " + code)
            expected_kg = (row["co2_metric_tonnes_annual"] * 1000 + row["ch4_kg_annual"] * 28 + row["n2o_kg_annual"] * 265) / row["gen_mwh"]
            checks.require(math.isclose(row["co2e_rate_lb_per_mwh"], expected_kg / .45359237, rel_tol=1e-10), "Alternative annual mass/unit arithmetic mismatch: " + code)
            ar6_mass = row["co2_metric_tonnes_annual"] + (row["ch4_kg_annual"] * 29.8 + row["n2o_kg_annual"] * 273) / 1000
            checks.require(abs(row["published_co2e_metric_tonnes_annual"] - ar6_mass) <= .02 and math.isclose(row["published_ar6_rate_kg_per_mwh"], row["published_co2e_metric_tonnes_annual"] * 1000 / row["gen_mwh"], rel_tol=1e-10), "Published AR6 annual mass/rate does not reconcile: " + code)
            non_fossil_rate = (row["co2_metric_tonnes_annual"] * 1000 + row["ch4_kg_annual"] * 27 + row["n2o_kg_annual"] * 273) / row["gen_mwh"]
            checks.require(row["published_comparison_gwp_basis"] == "IPCC AR6 non-fossil methane" and abs(row["published_comparison_rate_kg_per_mwh"] - non_fossil_rate) <= .069, "Published companion rate/method does not reconcile: " + code)
    checks.require(set(objects) == expected_paths, "Missing or unknown release-object classes")
    return {"facilities": len(entries), "grid_authorities": len(codes), "scenario_cards": scenario_count,
            "archived_permit_conditions": archived_limits, "stale_permit_conditions": stale_limits,
            "texas_document_links": document_links, "source_health": [{k: s.get(k) for k in
                ("id", "status", "partial", "record_count", "retrieved_at", "observation_end", "published_at", "documents_failed", "note", "error")}
                for s in sources]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://wattdocket.com")
    parser.add_argument("--root-dir", type=Path)
    parser.add_argument("--expected-revision")
    parser.add_argument("--expected-release-id")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=6)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    report = {"started_at": now_iso(), "environment": str(args.root_dir.resolve()) if args.root_dir else args.origin,
              "status": "failed", "scope": "All public data objects named by a pinned immutable release manifest",
              "not_exercised": ["Browser journeys, authentication and frontend release parity",
                                "Independent re-download of external permit PDFs/private raw archives",
                                "Completeness of all US facilities, permits or actual operating measurements"]}
    checks, evidence = Checks(), []
    try:
        reader = Reader(args.root_dir, args.origin, args.timeout)
        initial_raw, manifest, pointer_evidence = reader.manifest("/data/current.json")
        report.update(release_id=manifest["release_id"], revision=manifest["revision"], built_at=manifest["built_at"],
                      manifest_sha256=sha(initial_raw), coverage=manifest["coverage"], pointer=pointer_evidence)
        base = manifest["base_path"]
        checks.require(base == "/data/releases/" + manifest["release_id"], "Manifest base path/release identity mismatch")
        checks.require(manifest.get("schema_version") == "2.2", "Unsupported manifest schema")
        if args.expected_revision:
            checks.require(manifest["revision"] == args.expected_revision, "Published source revision differs from expected")
        else:
            report["not_exercised"].append("Expected source revision was not supplied")
        if args.expected_release_id:
            checks.require(manifest["release_id"] == args.expected_release_id, "Published release differs from expected")
        immutable_raw, immutable, _ = reader.manifest(base + "/manifest.json")
        checks.require(immutable == manifest and immutable_raw == initial_raw, "Pointer/immutable manifest bytes differ")
        specs = manifest["objects"]
        if not 1 <= len(specs) <= 1000 or sum(s["bytes"] for s in specs.values()) > MAX_RELEASE_BYTES:
            raise ValueError("Manifest exceeds bounded object count/release size")
        report["objects_expected"] = len(specs)
        objects, raw_objects = {}, {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = {pool.submit(fetch_object, reader, base, path, spec): path for path, spec in specs.items()}
            for future in as_completed(pending):
                path = pending[future]
                try:
                    obj, raw, record = future.result()
                    objects[path], raw_objects[path] = obj, raw
                    evidence.append(record)
                except Exception as exc:
                    checks.require(False, path + ": " + type(exc).__name__ + ": " + str(exc))
        checks.require(len(objects) == len(specs), "Not every object passed byte/hash/JSON/MIME checks")
        report.update(objects_verified=len(objects), uncompressed_bytes_verified=sum(len(b) for b in raw_objects.values()),
                      gzip_bytes_verified=sum(e["gzip_bytes"] for e in evidence))
        if len(objects) == len(specs):
            report["data_checks"] = validate_data(objects, raw_objects, manifest, checks)
        else:
            report["not_exercised"].append("Semantic checks blocked by failed fetch/integrity")
        final_raw, final_manifest, _ = reader.manifest("/data/current.json")
        report["pointer_unchanged"] = final_raw == initial_raw
        checks.require(final_raw == initial_raw, "Current pointer changed; pinned result is not the current release")
        report["final_release_id"] = final_manifest.get("release_id")
        report["status"] = "passed" if checks.failures == 0 else "failed"
    except Exception as exc:
        checks.require(False, type(exc).__name__ + ": " + str(exc))
    report.update(completed_at=now_iso(), checks=checks.count, failures=checks.failures, errors=checks.errors,
                  errors_truncated=checks.failures > len(checks.errors), objects=sorted(evidence, key=lambda e: e["path"]))
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
