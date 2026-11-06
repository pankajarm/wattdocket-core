"""Independent release checks for peak demand; no source-parser imports.

Nebraska literals are separately approved visible facts from the exact public
PDFs. NYC quantities are checked against native publisher cells and identifiers.
Neither check authorizes allocating a campus/meter value to an EPA record.
"""
from datetime import datetime, timezone
from decimal import Decimal
import math
import re
from urllib.parse import urlparse


class PowerEvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PowerEvidenceError("Peak demand evidence: " + message)


# Independent visible-page review by the release owner, November 4, 2026.
_NE_FACTS = {
    "ne-dcr:106518": ("8e3ddea4d77198935ef2a045492c020b6e008955357df170b607663c77f12d7b",
        254.5, "Fireball Group LLC", "Google LLC", "14865 Gold Coast Road", "Papillion", "68138", "data_center", 2,
        "2026-11-02", "20260102836"),
    "ne-dcr:90045": ("97fb3e1284c1c82ea64c6b92d868c670621af2eb238bbe9e2b77670de6d92088",
        52.65, "Agate LLC", "Google LLC", "5475 Cloud Court", "Lincoln", "68514", "data_center", 2,
        "2026-11-02", "20260102842"),
    "ne-dcr:114235": ("4d3e6aa7be574138f7c035bc50c525608215b70031cbe1b8cb22d9083000ad69",
        99, "MARA Holdings, Inc. - Kearney", "MARA Holdings, Inc.", "3215 Global Dr. Pl.", "Kearney", "68847", "cryptocurrency_mining", 1,
        "2026-09-16", "20260094036"),
}


def validate_demand(item, site):
    require(item.get("site_id") == site.get("site_id") and bool(site.get("site_id")), "site identity differs")
    require(item.get("metric") == "electricity_peak_demand_reported", "unknown metric")
    value, year = item.get("value"), item.get("reporting_year")
    require(type(value) in (int, float) and math.isfinite(value) and value > 0, "reported demand is missing, zero or invalid")
    require(type(year) is int and 2000 <= year < datetime.now(timezone.utc).year, "reporting year is incomplete or invalid")
    require(item.get("period_start") == f"{year}-01-01T00:00:00Z" and item.get("period_end") == f"{year+1}-01-01T00:00:00Z", "annual period differs")
    require(item.get("demand_id") == f"{site['site_id']}:{year}:electricity_peak_demand_reported", "demand ID differs from source/year")
    require(item.get("interval_minutes") is None and item.get("is_meter_observation") is False, "annual summary claims interval telemetry")
    require(site.get("inventory_record_ids") == [], "unreviewed inventory allocation")
    require(item.get("source_url") == site.get("source_url") and item.get("source_sha256") == site.get("source_sha256"), "site/demand source differs")
    receipt = item.get("source_receipt") or {}
    require(receipt.get("url") == item.get("source_url") and receipt.get("sha256") == item.get("source_sha256") and
            receipt.get("raw_key") == f"raw/{item.get('source_sha256')}.gz" and receipt.get("retrieved_at") == item.get("retrieved_at") and
            bool(receipt.get("retrieved_at")), "original source receipt differs")
    sid = item.get("source_id")
    require(sid == site.get("source_id"), "source partition differs")
    endpoint = urlparse(item.get("source_url") or "")
    address = site.get("address") or {}
    require(address.get("country") == "US", "reporting country changed")
    if sid == "nebraska_facility_demand":
        require(site["site_id"] in _NE_FACTS, "unreviewed Nebraska facility")
        sha, peak, name, operator, street, city, postal, kind, page, docdate, docnum = _NE_FACTS[site["site_id"]]
        require(item.get("source_sha256") == sha and value == peak and item.get("unit") == "MW" and year == 2025,
                "Nebraska value/unit/year does not match the reviewed visible PDF")
        require(endpoint.scheme == "https" and endpoint.hostname == "ecmp.nebraska.gov" and endpoint.path.startswith("/PublicAccess/api/Document/"),
                "Nebraska source is not the official public document endpoint")
        require(site.get("name") == name and site.get("operator") == operator and site.get("facility_type") == kind,
                "Nebraska facility/operator/type changed")
        require(address.get("street") == street and address.get("city") == city and address.get("state") == "NE" and address.get("postal_code") == postal,
                "Nebraska physical reporting address changed")
        require((site.get("source_identifiers") or {}).get("document_number") == docnum and
                (site.get("source_identifiers") or {}).get("nebraska_deq_facility_id") == site["site_id"].split(":")[1],
                "Nebraska document/program identity changed")
        require(item.get("source_page") == page and item.get("reviewed_document_date") == docdate and
                item.get("representation") == "reviewed_visible_pdf", "Nebraska visible-page/date attribution changed")
        require(item.get("measurement_boundary") == "reported_campus_maximum" and item.get("whole_facility") is True and
                item.get("reporting_scope") == site.get("reporting_scope") == "campus", "Nebraska campus peak boundary changed")
        require(item.get("peak_month") is None and item.get("publisher_meter_id") is None and
                item.get("reporting_basis") == "operator_reported_annual_peak", "Nebraska invents meter/time/billing evidence")
        public = item.get("public_access_evidence") or {}
        require(public == site.get("public_access_evidence") and public.get("redacted_values_used") is False and
                public.get("document_date") == docdate and public.get("public_index_url") == "https://ecmp.nebraska.gov/PublicAccess/index.html?MyQueryID=340",
                "Nebraska public release evidence missing or inconsistent")
        if operator == "Google LLC":
            require(public.get("returned_to_public_file_at") == "2026-11-02" and public.get("stamp_page") == 1 and
                    public.get("stamp_text") == "RETURN TO PUBLIC FILE", "Google report lacks its reviewed public-return stamp")
        else:
            require(public.get("returned_to_public_file_at") is None and "cryptocurrency" in str(item.get("note", "")).lower(),
                    "Mining facility classification or public-document history misrepresented")
    elif sid == "nyc_facility_demand":
        native = site.get("raw_source_row")
        require(isinstance(native, dict), "NYC native row missing")
        pid = str(native.get("property_id", ""))
        require(bool(re.fullmatch(r"[0-9]+", pid)) and int(pid) > 0 and site["site_id"] == "nyc:" + pid and
                site.get("related_annual_location_id") == "nyc-ll84:" + pid, "NYC native property identity changed")
        require(site.get("operator") is None and site.get("name") == (native.get("property_name") or native.get("address_1")),
                "NYC publisher name changed or unsupported operator inferred")
        require(endpoint.scheme == "https" and endpoint.hostname == "data.cityofnewyork.us" and endpoint.path == "/resource/5zyy-y8am.json",
                "NYC source is not the reviewed publisher dataset")
        require(str(native.get("report_year")) == str(year) and str(native.get("year_ending", ""))[:10] == f"{year}-12-31", "NYC source year differs")
        require(address.get("street") == native.get("address_1") and address.get("city") == native.get("city") and address.get("state") == "NY" and
                bool(address.get("street")) and bool(address.get("city")), "NYC native physical address changed")
        for field in ("property_id", "address_1", "city", "nyc_borough_block_and_lot", "nyc_building_identification"):
            require((site.get("source_identifiers") or {}).get(field) == native.get(field), "NYC source identifier differs: " + field)
        require("Data Center" in [native.get(f) for f in ("primary_property_type_self", "primary_property_type", "largest_property_use_type")],
                "NYC classification fell outside reviewed selection")
        flags = ("alert_energy_meter_has_less", "alert_energy_meter_has_gaps", "alert_energy_meter_has",
                 "alert_energy_no_meters", "alert_energy_meter_has_single")
        require(native.get("construction_status") == "Existing" and native.get("metered_areas_energy") == "Whole Property" and
                all(native.get(f) == "Ok" for f in flags), "NYC warning/incomplete native flags cannot publish a positive demand")
        cell = native.get("annual_maximum_demand_kw")
        require(not isinstance(cell, bool) and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(cell))) and
                Decimal(str(value)) == Decimal(str(cell)) and item.get("unit") == "kW", "NYC value/unit differs from native peak cell")
        month, meter = native.get("annual_maximum_demand_mm"), native.get("annual_maximum_demand_meter")
        require(bool(re.fullmatch(r"(?:0[1-9]|1[0-2])/" + str(year), str(month))), "NYC source peak month invalid")
        match = re.fullmatch(r".+ \(([0-9]+)\)", str(meter))
        require(match is not None and item.get("publisher_meter_id") == match.group(1) and item.get("publisher_meter_name_id") == meter and
                item.get("peak_month") == f"{year}-{month[:2]}", "NYC peak month/meter differs from native record")
        require(item.get("measurement_boundary") == "largest_individual_meter_entry" and item.get("whole_facility") is False and
                item.get("reporting_scope") == site.get("reporting_scope") == "meter" and item.get("demand_coverage_verified") is False,
                "NYC individual-meter entry promoted to whole-facility peak")
        require(item.get("reporting_basis") == "publisher_reported_individual_meter_annual_maximum" and
                item.get("energy_estimates_flag") == native.get("estimated_values_energy"), "NYC demand basis/estimate flag changed")
    else:
        raise PowerEvidenceError("Peak demand evidence: unreviewed source partition")
