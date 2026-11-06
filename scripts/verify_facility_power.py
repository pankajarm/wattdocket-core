"""Independent checks of public power objects; no ingestion/builder imports.

These checks reconcile published evidence and aggregates. They do not claim
to reread an archived workbook/PDF or independently calibrate a source meter.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
from urllib.parse import urlparse

BASE_COLUMNS = ["timestamp_utc", "value", "source_row", "source_timestamp", "source_timezone"]
ESIF_COLUMNS = BASE_COLUMNS + ["sample_count", "max_gap_seconds", "sample_sum_kw", "duplicate_timestamp_count"]
POWER_SOURCES = {"inl_c3_power", "nrel_esif_power", "nyc_facility_demand", "nebraska_facility_demand"}
COVERAGE_KEYS = {"power_reporting_sites", "power_interval_series", "power_interval_readings", "power_missing_intervals",
                 "reported_peak_demand_observations", "reported_campus_peak_observations", "live_facility_meter_series"}
# Independently reviewed visible 2025 report cells, bound to immutable documents.
NEBRASKA_PEAKS = {
    "ne-dcr:106518": (254.5, "8e3ddea4d77198935ef2a045492c020b6e008955357df170b607663c77f12d7b", "14865 Gold Coast Road"),
    "ne-dcr:90045": (52.65, "97fb3e1284c1c82ea64c6b92d868c670621af2eb238bbe9e2b77670de6d92088", "5475 Cloud Court"),
    "ne-dcr:114235": (99, "4d3e6aa7be574138f7c035bc50c525608215b70031cbe1b8cb22d9083000ad69", "3215 Global Dr. Pl."),
}


def _num(value):
    return type(value) in (int, float) and math.isfinite(value)


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.utcoffset() is not None else None
    except ValueError:
        return None


def _url(value):
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _normalized(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _receipt(receipt, url, digest, checks, context):
    receipt = receipt if isinstance(receipt, dict) else {}
    checks.require(_url(url) and isinstance(digest, str) and bool(re.fullmatch("[a-f0-9]{64}", digest)), context + ": invalid source URL/hash")
    checks.require(receipt.get("url") == url and receipt.get("sha256") == digest and
                   receipt.get("raw_key") == f"raw/{digest}.gz" and _time(receipt.get("retrieved_at")) is not None,
                   context + ": immutable capture receipt differs")
    return receipt


def _provenance(item, source_map, checks, context):
    receipt = _receipt(item.get("source_receipt"), item.get("source_url"), item.get("source_sha256"), checks, context)
    checks.require(item.get("retrieved_at") == receipt.get("retrieved_at"), context + ": check date replaced original retrieval")
    checks.require(_text(item.get("source_locator")), context + ": missing source locator")
    sid = item.get("source_id")
    checks.require(sid in POWER_SOURCES and sid in source_map, context + ": unknown/unpublished source")
    source = source_map.get(sid, {})
    checks.require(item.get("checked_at") == source.get("last_success_at") and _time(item.get("checked_at")) is not None,
                   context + ": check date differs from source health")
    checks.require(item.get("collection_status") == source.get("status"), context + ": collection status differs")
    retrieved, checked = _time(item.get("retrieved_at")), _time(item.get("checked_at"))
    checks.require(retrieved is not None and checked is not None and retrieved <= checked, context + ": impossible capture/check chronology")


def _no_allocation(item, checks, context):
    checks.require(not item.get("inventory_record_ids") and not item.get("record_id") and
                   item.get("inventory_allocation") is not True and item.get("facility_binding_verified") is not True,
                   context + ": unverified inventory allocation")


def _site(site, source_map, checks):
    context = "Power site " + str(site.get("site_id"))
    address = site.get("address") or {}
    checks.require(all(_text(address.get(k)) for k in ("street", "city", "state", "country")) and address.get("country") == "US",
                   context + ": physical address missing")
    checks.require(_text(site.get("name")) and _text(site.get("reporting_boundary")), context + ": identity/boundary missing")
    checks.require(site.get("inventory_record_ids") == [], context + ": inventory binding lacks an audited resolver")
    _no_allocation(site, checks, context)
    source = source_map.get(site.get("source_id"), {})
    checks.require(site.get("source_id") in POWER_SOURCES and site.get("checked_at") == source.get("last_success_at")
                   and _time(site.get("checked_at")) is not None, context + ": source/check date missing")
    evidence = site.get("identity_evidence") or []
    checks.require(isinstance(evidence, list) and bool(evidence), context + ": no physical identity proof")
    address_supported = False
    for proof in evidence:
        if not isinstance(proof, dict):
            checks.require(False, context + ": malformed identity proof")
            continue
        _receipt(proof.get("receipt"), proof.get("url"), proof.get("sha256"), checks, context + " identity")
        fields = proof.get("source_fields") or {}
        fields = fields if isinstance(fields, dict) else {}
        checks.require(_text(proof.get("locator")) and (_text(proof.get("quote")) or isinstance(fields, dict) and bool(fields)),
                       context + ": identity proof lacks locator/content")
        street = _normalized(address.get("street"))
        address_supported |= bool(street and (street in _normalized(proof.get("quote", "")) or
                                             street == _normalized(fields.get("address_1"))))
    checks.require(address_supported, context + ": source proof does not establish the displayed street address")


def _inl_timestamp(row, current, checks, context):
    checks.require(_text(row[2]) and bool(re.fullmatch(r"Power!A([2-9]|[1-9]\d+):B\1", row[2])), context + ": INL worksheet locator invalid")
    try:
        local = datetime.fromisoformat(row[3])
        if local.tzinfo is not None or row[4] not in {"MST", "MDT"}:
            raise ValueError("unreviewed source clock")
        midnight_hour = local.replace(minute=0, second=0, microsecond=0)
        rounded = midnight_hour + timedelta(hours=(local - midnight_hour).total_seconds() >= 1800)
        expected = rounded.replace(tzinfo=timezone(timedelta(hours=-7 if row[4] == "MST" else -6)))
        okay = abs((rounded - local).total_seconds()) < .001 and expected == current
    except (TypeError, ValueError):
        okay = False
    checks.require(okay, context + ": INL UTC differs from original source clock")


def _esif_source_time(value):
    match = re.fullmatch(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{2}) (\d{4}) (\d{2}:\d{2}:\d{2}) GMT(-0[67]00) \(Mountain (Standard|Daylight) Time\)", value)
    if not match:
        return None
    weekday, month, day, year, clock, offset, season = match.groups()
    if (offset, season) not in {("-0700", "Standard"), ("-0600", "Daylight")}:
        return None
    try:
        result = datetime.strptime(f"{month} {day} {year} {clock} {offset}", "%b %d %Y %H:%M:%S %z")
        return result if result.strftime("%a") == weekday else None
    except ValueError:
        return None


def _esif_row(row, current, checks, context):
    count, gap, total, duplicates = row[5:9]
    controls = type(count) is int and 0 <= count <= 3600 and type(gap) is int and 0 <= gap <= 3600 and \
        type(duplicates) is int and 0 <= duplicates <= 3600 and _num(total) and 0 <= total <= count * 100_000
    checks.require(controls, context + ": invalid ESIF sample aggregates")
    if not controls:
        return
    enough = count >= 45 and gap <= 300 and duplicates == 0
    checks.require((row[1] is not None) == enough, context + ": ESIF coverage/duplicate gate violated")
    if enough and _num(row[1]):
        checks.require(math.isclose(row[1], total / count, rel_tol=1e-12, abs_tol=1e-9), context + ": ESIF arithmetic mean differs from sample sum/count")
    if all(value is None for value in row[2:5]):
        checks.require(row[1] is None and count == 0 and total == 0 and gap == 3600 and duplicates == 0,
                       context + ": missing-hour provenance masks samples")
        return
    checks.require(all(_text(v) for v in row[2:5]), context + ": ESIF partial provenance missing")
    if not all(_text(v) for v in row[2:5]) or current is None:
        return
    locator = re.fullmatch(r"esif\.influx\.buildingData\.PUE\.combined\.csv:rows (\d+)-(\d+)", row[2])
    checks.require(locator is not None and 2 <= int(locator[1]) <= int(locator[2]), context + ": ESIF row range invalid")
    native = row[3].split(" | ")
    endpoints = [_esif_source_time(value) for value in native]
    checks.require(len(endpoints) == 2 and all(value is not None and current <= value < current + timedelta(hours=1) for value in endpoints),
                   context + ": ESIF source samples outside UTC hour")
    if len(endpoints) == 2 and all(value is not None for value in endpoints):
        checks.require(endpoints[0] <= endpoints[1], context + ": reversed native sample range")
        zones = {"GMT" + value.strftime("%z") + " (Mountain " + ("Standard" if value.utcoffset() == timedelta(hours=-7) else "Daylight") + " Time)" for value in endpoints}
        checks.require(set(row[4].split(" | ")) == zones, context + ": displayed source timezone differs")


def _series(item, source_map, checks, built, release_id):
    context = "Power series " + str(item.get("series_id"))
    _provenance(item, source_map, checks, context)
    _no_allocation(item, checks, context)
    checks.require(item.get("release_id") == release_id, context + ": cross-release interval object")
    checks.require(item.get("metric") == "electricity_power" and item.get("unit") == "kW", context + ": power unit/metric changed")
    checks.require(item.get("historical_snapshot") is True and item.get("whole_facility") is False, context + ": historical/component meter overclaimed")
    checks.require(item.get("interval_minutes") == 60 and all(_text(item.get(k)) for k in
                   ("measurement_boundary", "measurement_basis", "time_basis", "methodology_note")), context + ": method/boundary missing")
    sid = item.get("source_id")
    expected = BASE_COLUMNS if sid == "inl_c3_power" else ESIF_COLUMNS if sid == "nrel_esif_power" else None
    checks.require(expected is not None and item.get("columns") == expected, context + ": unknown reading column contract")
    rows = item.get("readings") or []
    if not checks.require(isinstance(rows, list) and 0 < len(rows) <= 150_000 and expected is not None, context + ": invalid row count/source"):
        return {"measured": 0, "missing": 0}
    if sid == "inl_c3_power":
        checks.require(item.get("measurement_basis") == "publisher_reported_meter_power" and item.get("license") == "Creative Commons Attribution",
                       context + ": INL source method/license changed")
        checks.require("Energy" in item.get("methodology_note", "") and "unspecified" in item.get("time_basis", ""), context + ": INL source ambiguity hidden")
        checks.require("not established" in item.get("measurement_boundary", ""), context + ": INL unspecified meter boundary hidden")
    else:
        checks.require(item.get("measurement_basis") == "derived_hourly_arithmetic_mean_of_metered_samples",
                       context + ": derived ESIF means presented as native readings")
        checks.require("IT equipment" in item.get("measurement_boundary", "") and "excludes cooling" in item.get("measurement_boundary", ""),
                       context + ": ESIF component meter boundary inflated")
        aggregation = item.get("aggregation") or {}
        checks.require(aggregation.get("method") == "arithmetic_mean" and aggregation.get("minimum_numeric_samples") == 45 and
                       aggregation.get("maximum_gap_including_edges_seconds") == 300 and aggregation.get("missing_values_imputed") is False and
                       aggregation.get("annual_energy_derivable") is False, context + ": ESIF derivation/coverage policy changed")
        for evidence in item.get("method_evidence", []):
            _receipt(evidence.get("receipt"), evidence.get("url"), evidence.get("sha256"), checks, context + " method")
        checks.require(len(item.get("method_evidence", [])) >= 2, context + ": ESIF metrology evidence absent")
        license_info = item.get("license") or {}
        if not isinstance(license_info, dict):
            checks.require(False, context + ": ESIF license notice absent")
            license_info = {}
        notice = license_info.get("notice", "")
        checks.require(_text(notice) and hashlib.sha256(notice.encode()).hexdigest() == license_info.get("notice_sha256") and
                       license_info.get("requires_full_notice_with_copies") is True and _text(license_info.get("attribution")),
                       context + ": ESIF complete license notice/hash/credit missing")
        _receipt(license_info.get("receipt"), license_info.get("url"), license_info.get("source_sha256"), checks, context + " license")
    previous, values, missing, timestamps = None, [], 0, []
    for number, row in enumerate(rows):
        label = context + " row " + str(number)
        if not checks.require(isinstance(row, list) and len(row) == len(expected), label + ": columns differ"):
            continue
        current = _time(row[0])
        checks.require(current is not None and current.utcoffset() == timedelta(0) and built is not None and current <= built,
                       label + ": invalid/future/non-UTC timestamp")
        if current is not None:
            checks.require(previous is None or current - previous == timedelta(hours=1), label + ": duplicate, unordered or skipped hour")
            previous = current
        timestamps.append(row[0])
        if row[1] is None:
            missing += 1
        elif checks.require(_num(row[1]) and 0 <= row[1] <= 1_000_000, label + ": invalid measured kW"):
            values.append(row[1])
        if sid == "inl_c3_power":
            _inl_timestamp(row, current, checks, label)
        else:
            _esif_row(row, current, checks, label)
    checks.require(all(type(item.get(key)) is int for key in ("reading_count", "measured_count", "missing_count")) and
                   bool(values) and item.get("reading_count") == len(rows) and item.get("measured_count") == len(values)
                   and item.get("missing_count") == missing, context + ": row/measured/missing counts differ")
    checks.require(bool(timestamps) and item.get("observed_start") == timestamps[0] and item.get("observed_end") == timestamps[-1],
                   context + ": observation range differs from bins")
    checks.require(bool(values) and item.get("maximum_reported_kw") == max(values) and item.get("minimum_reported_kw") == min(values),
                   context + ": maximum/minimum summaries differ")
    return {"measured": len(values), "missing": missing}


def _demand(item, site, source_map, checks, built):
    context = "Power demand " + str(item.get("demand_id"))
    _provenance(item, source_map, checks, context)
    _no_allocation(item, checks, context)
    value, year = item.get("value"), item.get("reporting_year")
    checks.require(item.get("metric") == "electricity_peak_demand_reported" and item.get("unit") in {"kW", "MW"} and
                   _num(value) and 0 < value <= 1_000_000, context + ": invalid positive reported demand/unit")
    checks.require(type(year) is int and built is not None and 2000 <= year < built.year and
                   item.get("period_start") == f"{year}-01-01T00:00:00Z" and item.get("period_end") == f"{year+1}-01-01T00:00:00Z",
                   context + ": invalid annual reporting period")
    checks.require(item.get("is_meter_observation") is False and item.get("interval_minutes") is None and _text(item.get("note")),
                   context + ": annual summary promoted to interval metering")
    boundary = item.get("measurement_boundary")
    checks.require(boundary in {"largest_individual_meter_entry", "reported_campus_maximum"} and
                   item.get("whole_facility") is (boundary == "reported_campus_maximum"), context + ": demand scope conflicts with boundary")
    sid = item.get("source_id")
    if sid == "nyc_facility_demand":
        native = site.get("raw_source_row") or {}
        meter = native.get("annual_maximum_demand_meter", "")
        match = re.fullmatch(r".+ \((\d+)\)", str(meter))
        checks.require(boundary == "largest_individual_meter_entry" and item.get("reporting_scope") == "meter" and
                       item.get("unit") == "kW" and item.get("reporting_basis") == "publisher_reported_individual_meter_annual_maximum"
                       and item.get("demand_coverage_verified") is False, context + ": NYC individual-meter boundary inflated")
        try:
            native_value = float(native.get("annual_maximum_demand_kw"))
        except (TypeError, ValueError):
            native_value = None
        checks.require(_num(native_value) and value == native_value and str(native.get("report_year")) == str(year), context + ": NYC native value/year differs")
        month = native.get("annual_maximum_demand_mm", "")
        checks.require(isinstance(month, str) and re.fullmatch(r"(?:0[1-9]|1[0-2])/" + str(year), month) is not None and
                       item.get("peak_month") == f"{year}-{month[:2]}" and match is not None and
                       item.get("publisher_meter_id") == match[1] and item.get("publisher_meter_name_id") == meter,
                       context + ": NYC meter/month evidence differs")
        checks.require(str(site.get("source_identifiers", {}).get("property_id")) == str(native.get("property_id")) and
                       site.get("address", {}).get("street") == native.get("address_1") and
                       site.get("address", {}).get("city") == native.get("city"), context + ": NYC property/address differs from native row")
        flags = ("alert_energy_meter_has_less", "alert_energy_meter_has_gaps", "alert_energy_meter_has",
                 "alert_energy_no_meters", "alert_energy_meter_has_single")
        checks.require(native.get("construction_status") == "Existing" and native.get("metered_areas_energy") == "Whole Property" and
                       all(native.get(field) == "Ok" for field in flags) and str(native.get("year_ending", ""))[:10] == f"{year}-12-31",
                       context + ": NYC native completeness/period no longer qualifies")
        checks.require("Data Center" in [native.get(field) for field in
                       ("primary_property_type_self", "primary_property_type", "largest_property_use_type")],
                       context + ": NYC source property classification changed")
    elif sid == "nebraska_facility_demand":
        reviewed = NEBRASKA_PEAKS.get(item.get("site_id"))
        checks.require(reviewed is not None and value == reviewed[0] and item.get("source_sha256") == reviewed[1] and
                       site.get("address", {}).get("street") == reviewed[2] and year == 2025 and item.get("unit") == "MW",
                       context + ": Nebraska visible reviewed figure/document/site differs")
        checks.require(boundary == "reported_campus_maximum" and item.get("reporting_scope") == "campus" and
                       item.get("reporting_basis") == "operator_reported_annual_peak" and item.get("peak_month") is None
                       and item.get("publisher_meter_id") is None, context + ": Nebraska peak timing/meter scope invented")
        access = item.get("public_access_evidence") or {}
        checks.require(access.get("redacted_values_used") is False, context + ": public/redaction provenance missing")
        if item.get("site_id") in {"ne-dcr:106518", "ne-dcr:90045"}:
            checks.require(access.get("returned_to_public_file_at") == "2026-11-02" and access.get("stamp_text") == "RETURN TO PUBLIC FILE",
                           context + ": Nebraska public-return evidence missing")
        if item.get("site_id") == "ne-dcr:114235":
            checks.require(site.get("facility_type") == "cryptocurrency_mining" and "cryptocurrency" in item.get("note", ""),
                           context + ": mining property relabeled as an AI/cloud data center")
    else:
        checks.require(False, context + ": unreviewed demand source")


def validate_facility_power(objects, coverage, checks):
    """Reconcile all power public objects and return their expected paths."""
    actual_paths = {path for path in objects if path == "facility-power.json" or path.startswith("power/")}
    index = objects.get("facility-power.json")
    if index is None:
        checks.require(not actual_paths and all(coverage.get(k, 0) == 0 for k in COVERAGE_KEYS), "Power objects/coverage exist without index")
        return set()
    if not checks.require(isinstance(index, dict), "Facility-power index is malformed"):
        return {"facility-power.json"}
    status = objects.get("data-status.json") or {}
    sources = {s.get("id"): s for s in status.get("sources", [])}
    checks.require(index.get("schema_version") == "1" and index.get("release_id") == status.get("release_id") and
                   index.get("built_at") == status.get("built_at"), "Facility-power release/schema differs from data status")
    built = _time(index.get("built_at"))
    sites, ids = {}, set()
    for site in index.get("sites", []):
        sid = site.get("site_id")
        checks.require(_text(sid) and sid not in sites, "Power site ID missing/duplicate")
        _site(site, sources, checks)
        sites[sid] = site
    expected_paths, used_sites = {"facility-power.json"}, set()
    total_measured, total_missing, counts = 0, 0, {}
    for descriptor in index.get("series", []):
        sid = descriptor.get("series_id", "")
        path = "power/" + sid + ".json"
        checks.require(bool(re.fullmatch("[a-z0-9_-]+", sid)) and sid not in ids and descriptor.get("readings_path") == path,
                       "Power series ID/path missing, unsafe or duplicate")
        ids.add(sid)
        expected_paths.add(path)
        item = objects.get(path)
        if not checks.require(isinstance(item, dict), "Power descriptor lacks interval object: " + sid):
            continue
        checks.require({k: v for k, v in descriptor.items() if k != "readings_path"} == {k: v for k, v in item.items() if k != "readings"},
                       "Power descriptor differs from interval object: " + sid)
        site = sites.get(item.get("site_id"), {})
        checks.require(bool(site) and site.get("source_id") == item.get("source_id"), "Power series lacks its own source site: " + sid)
        used_sites.add(item.get("site_id"))
        result = _series(item, sources, checks, built, index.get("release_id"))
        total_measured += result["measured"]
        total_missing += result["missing"]
        counts[item.get("source_id")] = counts.get(item.get("source_id"), 0) + result["measured"]
    for demand in index.get("demands", []):
        did = demand.get("demand_id")
        checks.require(_text(did) and did not in ids, "Power demand ID missing/duplicate")
        ids.add(did)
        site = sites.get(demand.get("site_id"), {})
        checks.require(bool(site) and site.get("source_id") == demand.get("source_id"), "Demand lacks its own source site: " + str(did))
        used_sites.add(demand.get("site_id"))
        _demand(demand, site, sources, checks, built)
        counts[demand.get("source_id")] = counts.get(demand.get("source_id"), 0) + 1
    checks.require(used_sites == set(sites), "Power site count includes unreferenced sites")
    expected_coverage = {"power_reporting_sites": len(sites), "power_interval_series": len(index.get("series", [])),
        "power_interval_readings": total_measured, "power_missing_intervals": total_missing,
        "reported_peak_demand_observations": len(index.get("demands", [])),
        "reported_campus_peak_observations": sum(d.get("whole_facility") is True for d in index.get("demands", [])),
        "live_facility_meter_series": 0}
    for key, value in expected_coverage.items():
        checks.require(type(index.get("coverage", {}).get(key)) is int and type(coverage.get(key)) is int and
                       index.get("coverage", {}).get(key) == coverage.get(key) == value, "Power coverage mismatch: " + key)
    for sid, count in counts.items():
        checks.require(type(sources.get(sid, {}).get("record_count")) is int and sources.get(sid, {}).get("record_count") == count,
                       "Power source count differs from published quantities: " + str(sid))
    checks.require(actual_paths == expected_paths, "Power objects missing or unreferenced")
    for card in (objects.get("cards.json") or {}).values():
        for claim in card.get("claims", []):
            if claim.get("source_id") in POWER_SOURCES:
                checks.require(not _num(claim.get("value")), "Power source allocated a number to an unverified inventory card")
    return expected_paths
