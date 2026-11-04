"""Independent municipal cell/quality checks, without importing the adapters.

This verifies the emitted value against its retained publisher row. The main
publication gate separately checks the row's capture URL/hash/receipt. Neither
check audits utility meters or makes a property total a datacenter-only value.
"""
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import re
from urllib.parse import urlparse


class MunicipalEvidenceError(ValueError):
    pass


def _require(ok, reason):
    if not ok:
        raise MunicipalEvidenceError(reason)


def validate_municipal_observation(location, row):
    source = location.get("source_id")
    key = location.get("disclosure_location_id", "")
    municipal = source in {"nyc_benchmarking", "seattle_benchmarking"}
    if not municipal:
        _require(not key.startswith(("nyc-ll84:", "seattle-benchmarking:")), "Municipal reporting entity has the wrong source")
        return
    native = location.get("raw_source_row")
    _require(isinstance(native, dict) and native, "Municipal retained publisher row is missing")
    nyc = source == "nyc_benchmarking"
    fields = ({"electricity_consumption": ("electricity_use_grid_purchase_2", "kWh"),
               "water_use_reported": ("water_use_all_water_sources", "kgal"),
               "reported_ghg_total": ("total_location_based_ghg", "metric tons CO2e")} if nyc else
              {"electricity_consumption": ("electricity_kwh", "kWh"),
               "reported_ghg_total": ("totalghgemissions", "metric tons CO2e")})
    metric = row.get("metric")
    _require(metric in fields, "Municipal metric is not published by this source")
    field, unit = fields[metric]
    _require(row.get("source_field") == field and row.get("unit") == unit,
             "Municipal metric field or native unit changed")
    raw_value = native.get(field)
    _require(not isinstance(raw_value, bool) and re.fullmatch(r"\d+(?:\.\d+)?", str(raw_value or "0")) is not None
             and raw_value is not None and raw_value != "", "Municipal native quantity is absent or malformed")
    try:
        actual, expected = Decimal(str(row.get("value"))), Decimal(str(raw_value))
    except (InvalidOperation, ValueError):
        raise MunicipalEvidenceError("Municipal numeric cell is invalid") from None
    _require(actual.is_finite() and actual == expected, "Municipal observation differs from its native publisher cell")
    pid = native.get("property_id" if nyc else "osebuildingid")
    prefix = "nyc-ll84:" if nyc else "seattle-benchmarking:"
    _require(re.fullmatch(r"[1-9]\d*", str(pid or "")) is not None and key == prefix + str(pid),
             "Municipal native property identity differs")
    year = row.get("data_year")
    _require(isinstance(year, int) and not isinstance(year, bool) and 2010 <= year < datetime.now(timezone.utc).year and
             str(native.get("report_year" if nyc else "datayear")) == str(year),
             "Municipal observation differs from native reporting year")
    _require(row.get("period_start") == f"{year}-01-01T00:00:00Z" and
             row.get("period_end") == f"{year + 1}-01-01T00:00:00Z", "Municipal annual period changed")
    _require(row.get("disclosure_location_id") == key and row.get("source_id") == source and
             row.get("observation_id") == f"{key}:{year}:{metric}", "Municipal observation identity changed")
    url = urlparse(row.get("source_url", ""))
    _require(url.scheme == "https" and url.netloc == ("data.cityofnewyork.us" if nyc else "data.seattle.gov") and
             url.path == ("/resource/5zyy-y8am.json" if nyc else "/resource/teqw-tu6e.json"),
             "Municipal observation uses a different publisher dataset")
    _require(location.get("source_url") == row["source_url"] and location.get("source_sha256") == row.get("source_sha256"),
             "Municipal property and observation capture differ")
    buildings = native.get("number_of_buildings" if nyc else "numberofbuildings")
    _require(re.fullmatch(r"[1-9]\d*", str(buildings or "")) is not None, "Municipal native building count is invalid")
    expected_scope = "building" if int(buildings) == 1 else "campus"
    _require(row.get("reporting_scope") == location.get("reporting_scope") == expected_scope,
             "Municipal whole-property scope differs from native building count")
    _require(row.get("is_meter_observation") is False and row.get("metered_share_known") is False,
             "Municipal reported property total promoted to audited meter data")
    if nyc:
        _require(str(native.get("year_ending", ""))[:10] == f"{year}-12-31", "NYC native year end differs")
        _require("Data Center" in [native.get(k) for k in
                 ("primary_property_type_self", "primary_property_type", "largest_property_use_type")],
                 "NYC source classification is outside the reviewed selection")
        _require(native.get("construction_status") == "Existing", "NYC nonexisting property observation is not qualified")
        water = metric == "water_use_reported"
        area = "metered_areas_water" if water else "metered_areas_energy"
        _require(native.get(area) == "Whole Property", "NYC meters do not cover the whole reporting property")
        flags = (("alert_water_meter_has_less",) if water else
                 ("alert_energy_meter_has_less", "alert_energy_meter_has_gaps", "alert_energy_meter_has",
                  "alert_energy_no_meters", "alert_energy_meter_has_single"))
        _require(all(native.get(f) == "Ok" for f in flags), "NYC publisher meter completeness flag is not qualified")
        estimate_field = "estimated_values_water" if water else "estimated_values_energy"
        estimate = native.get(estimate_field)
        basis = ("publisher_calculated_from_reported_energy" if metric == "reported_ghg_total" else
                 "reported_annual_usage_includes_estimates" if estimate == "Yes" else "reported_annual_usage")
        _require(f"Publisher {estimate_field}: {estimate or 'unknown'}." in row.get("methodology_note", ""),
                 "NYC estimate limitation differs from the native flag")
        quality_fields = (*flags, area, estimate_field, "construction_status")
    else:
        _require(native.get("state") == "WA" and "Data Center" in [native.get("epapropertytype"), native.get("largestpropertyusetype")],
                 "Seattle publisher geography/classification changed")
        _require(native.get("compliancestatus") == "Compliant" and native.get("complianceissue") == "No Issue" and
                 native.get("demolished") is False, "Seattle publisher completeness/demolition flag is not qualified")
        basis = "publisher_calculated_from_reported_energy" if metric == "reported_ghg_total" else "reported_annual_usage"
        quality_fields = ("compliancestatus", "complianceissue", "demolished")
    _require(row.get("reporting_basis") == basis, "Municipal reporting/estimate basis differs from native evidence")
    for emitted in (location.get("source_quality") or {}, row.get("source_quality") or {}):
        _require(all(k in emitted and emitted[k] == native.get(k) for k in quality_fields),
                 "Municipal published quality flags differ from the native row")
