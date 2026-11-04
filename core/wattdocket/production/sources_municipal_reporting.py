"""Annual municipal building reports; never datacenter-only or live meter data.

The city classifies each reporting property. Mixed uses, meter-quality flags,
parent IDs and missing quantities stay visible. These adapters never bind an
observation to an EPA record or sum overlapping reporting properties.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

from .models import Fetcher, SourceResult

NYC_ID = "5zyy-y8am"
SEATTLE_ID = "teqw-tu6e"
NYC_BASE = "https://data.cityofnewyork.us"
SEATTLE_BASE = "https://data.seattle.gov"
NYC_PAGE = NYC_BASE + "/Environment/NYC-Building-Energy-and-Water-Data-Disclosure-for-/" + NYC_ID
SEATTLE_PAGE = SEATTLE_BASE + "/Built-Environment/Building-Energy-Benchmarking-Data-2015-Present/" + SEATTLE_ID
NYC_FILTER = "(primary_property_type_self='Data Center' OR primary_property_type='Data Center' OR largest_property_use_type='Data Center')"
SEATTLE_FILTER = "(epapropertytype='Data Center' OR largestpropertyusetype='Data Center')"
NYC_METRICS = {
    "electricity_consumption": ("electricity_use_grid_purchase_2", "kWh", "Electricity Use - Grid Purchase and Generated from Onsite Renewable Systems (kWh)"),
    "water_use_reported": ("water_use_all_water_sources", "kgal", "Water Use (All Water Sources) (kgal)"),
    "reported_ghg_total": ("total_location_based_ghg", "metric tons CO2e", "Total (Location-Based) GHG Emissions (Metric Tons CO2e)"),
}
SEATTLE_METRICS = {
    "electricity_consumption": ("electricity_kwh", "kWh", "Electricity(kWh)"),
    "reported_ghg_total": ("totalghgemissions", "metric tons CO2e", "TotalGHGEmissions"),
}
NYC_ENERGY_FLAGS = ("alert_energy_meter_has_less", "alert_energy_meter_has_gaps",
                    "alert_energy_meter_has", "alert_energy_no_meters", "alert_energy_meter_has_single")
NYC_FIELDS = (
    "report_year", "property_id", "property_name", "parent_property_id", "parent_property_name", "year_ending",
    "nyc_borough_block_and_lot", "nyc_building_identification", "address_1", "city", "postal_code",
    "primary_property_type_self", "primary_property_type", "list_of_all_property_use", "largest_property_use_type",
    "number_of_buildings", "construction_status", "metered_areas_energy", "metered_areas_water",
    "estimated_values_energy", "estimated_values_water", *NYC_ENERGY_FLAGS, "alert_water_meter_has_less",
    "alert_data_center_issue_with", "data_center_energy_estimates",
    "data_center_gross_floor_area", "property_gfa_self_reported", "property_gfa_calculated",
    "electricity_use_grid_purchase_2", "water_use_all_water_sources", "total_location_based_ghg",
    "direct_ghg_emissions_metric", "indirect_location_based_ghg", "report_generation_date",
)
SEATTLE_FIELDS = (
    "osebuildingid", "datayear", "buildingname", "buildingtype", "taxparcelidentificationnumber",
    "address", "city", "state", "zipcode", "numberofbuildings", "propertygfatotal", "propertygfabuildings",
    "selfreportgfatotal", "epapropertytype", "largestpropertyusetype", "largestpropertyusetypegfa",
    "secondlargestpropertyusetype", "secondlargestpropertyuse", "thirdlargestpropertyusetype",
    "thirdlargestpropertyusetypegfa", "electricity_kwh", "totalghgemissions", "compliancestatus",
    "complianceissue", "demolished",
)
NYC_SCOPE = ("Whole reporting property classified by the publisher as Data Center in at least one primary/largest-use field. "
             "It can include telecommunications equipment, offices, retail and other uses. Quantities are not isolated "
             "datacenter or IT-only energy/water. A property ID, tax parcel or building ID is not an EPA registration. "
             "No inventory allocation or complete national datacenter coverage is claimed.")
SEATTLE_SCOPE = ("Whole reporting property classified by the city as Data Center in its primary or largest-use field. "
                 "Telecommunications and other mixed uses may be included. These are building/property reports, not "
                 "datacenter-only or IT-only quantities. No EPA record match or national datacenter census is implied.")


class MunicipalReportingError(ValueError):
    pass


def _capture(fetcher: Fetcher, url: str):
    raw = fetcher.get_bytes(url)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise MunicipalReportingError("Municipal publisher returned invalid JSON") from exc
    if isinstance(data, dict) and (data.get("error") or data.get("errorCode") or data.get("code")):
        raise MunicipalReportingError("Municipal API returned an error")
    return data, hashlib.sha256(raw).hexdigest()


def _query(base, dataset, **params):
    return f"{base}/resource/{dataset}.json?" + urlencode(params)


def _integer(value, label, minimum=1, maximum=999999999):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value or "")):
        raise MunicipalReportingError(f"Invalid {label}")
    result = int(value)
    if not minimum <= result <= maximum:
        raise MunicipalReportingError(f"Out-of-range {label}")
    return result


def _number(value):
    if value is None or value in ("", "Not Available", "Not Applicable", "NA", "N/A"):
        return None
    if isinstance(value, bool) or not re.fullmatch(r"\d+(?:\.\d+)?", str(value)):
        raise MunicipalReportingError("Malformed or negative reported quantity")
    result = float(Decimal(str(value)))
    if not math.isfinite(result):
        raise MunicipalReportingError("Nonfinite reported quantity")
    return int(result) if result.is_integer() else result


def _latest_capture(fetcher, *, base, dataset, year_field, id_field, selection, fields, metrics, now,
                    page_size=100, max_rows=1000):
    """Select latest publisher year, verify row count and deterministic pages."""
    metadata_url = f"{base}/api/views/{dataset}.json"
    meta, meta_sha = _capture(fetcher, metadata_url)
    if not isinstance(meta, dict) or meta.get("id") != dataset:
        raise MunicipalReportingError("Municipal dataset identity changed")
    _integer(meta.get("rowsUpdatedAt"), "dataset revision timestamp", 1, 9999999999)
    columns = {x.get("fieldName"): x for x in meta.get("columns", [])}
    if not set(fields) <= set(columns):
        raise MunicipalReportingError("Municipal schema lost reviewed fields")
    for field, unit, label in metrics.values():
        if columns[field].get("name") != label:
            raise MunicipalReportingError("Municipal metric label/unit changed: " + field)
    latest_url = _query(base, dataset, **{"$select": year_field, "$order": year_field + " DESC", "$limit": 1})
    latest, latest_sha = _capture(fetcher, latest_url)
    if not isinstance(latest, list) or len(latest) != 1:
        raise MunicipalReportingError("Municipal latest reporting year unavailable")
    year = _integer(latest[0].get(year_field), "complete calendar year", 2010, now.year - 1)
    where = f"{year_field}='{year}' AND {selection}"
    count_url = _query(base, dataset, **{"$select": "count(*) AS n", "$where": where})
    count, count_sha = _capture(fetcher, count_url)
    if not isinstance(count, list) or len(count) != 1:
        raise MunicipalReportingError("Municipal query count unavailable")
    total = _integer(count[0].get("n"), "selected property count", 1, max_rows)
    rows, seen, page_receipts = [], set(), []
    for offset in range(0, total, page_size):
        url = _query(base, dataset, **{"$select": ",".join(fields), "$where": where,
            "$order": id_field, "$limit": page_size, "$offset": offset})
        page, sha = _capture(fetcher, url)
        if not isinstance(page, list) or len(page) != min(page_size, total - offset):
            raise MunicipalReportingError("Municipal page missing or truncated")
        for row in page:
            if not isinstance(row, dict):
                raise MunicipalReportingError("Municipal row is not an object")
            rid = str(_integer(row.get(id_field), "reporting property ID"))
            if rid in seen or str(row.get(year_field)) != str(year):
                raise MunicipalReportingError("Municipal page repeats a property or changes reporting year")
            seen.add(rid)
            rows.append((row, url, sha))
        page_receipts.append({"url": url, "sha256": sha, "rows": len(page)})
    # Recheck publisher revision after pagination. Equal count alone cannot
    # detect same-sized replacement pages from an upstream edit mid-capture.
    final_meta, _ = _capture(fetcher, metadata_url)
    if final_meta.get("rowsUpdatedAt") != meta.get("rowsUpdatedAt"):
        raise MunicipalReportingError("Municipal dataset changed during capture")
    return rows, year, {"metadata_url": metadata_url, "metadata_sha256": meta_sha,
        "dataset_revision": meta.get("rowsUpdatedAt"), "dataset_name": meta.get("name"),
        "latest_year_url": latest_url, "latest_year_sha256": latest_sha,
        "count_url": count_url, "count_sha256": count_sha, "selected_property_count": total,
        "pages": page_receipts, "selection": selection}


def _observation(key, metric, value, unit, year, scope, note, basis, row, field, url, sha, publisher, quality):
    return {"observation_id": f"{key}:{year}:{metric}", "disclosure_location_id": key,
        "metric": metric, "value": value, "unit": unit, "data_year": year,
        "period_start": f"{year}-01-01T00:00:00Z", "period_end": f"{year + 1}-01-01T00:00:00Z",
        "reporting_scope": scope, "reporting_basis": basis, "methodology_note": note,
        "source_url": url, "source_sha256": sha, "publisher": publisher,
        "source_field": field, "source_locator": f"Reporting property {key}; calendar year {year}; field {field}",
        "facility_binding_verified": False, "is_meter_observation": False,
        "metered_share_known": False, "source_quality": quality}


def parse_nyc_rows(captured_rows, year):
    locations, observations, withheld, seen = [], [], [], set()
    for row, url, sha in captured_rows:
        pid = str(_integer(row.get("property_id"), "NYC property ID"))
        if pid in seen:
            raise MunicipalReportingError("Duplicate NYC reporting property")
        seen.add(pid)
        key = "nyc-ll84:" + pid
        if str(row.get("report_year")) != str(year) or str(row.get("year_ending", ""))[:10] != f"{year}-12-31":
            raise MunicipalReportingError("NYC annual period disagrees with its calendar year")
        if "Data Center" not in [row.get(k) for k in ("primary_property_type_self", "primary_property_type", "largest_property_use_type")]:
            raise MunicipalReportingError("NYC query returned a property outside its reviewed classification")
        buildings = _integer(row.get("number_of_buildings"), "NYC building count", 1, 1000)
        scope = "building" if buildings == 1 else "campus"
        if not row.get("address_1") or not row.get("city"):
            raise MunicipalReportingError("NYC property lacks its publisher address")
        quality_fields = ("metered_areas_energy", "metered_areas_water", "estimated_values_energy", "estimated_values_water",
            *NYC_ENERGY_FLAGS, "alert_water_meter_has_less", "alert_data_center_issue_with",
            "data_center_energy_estimates", "construction_status")
        quality = {k: row.get(k) for k in quality_fields}
        base_reasons = [] if row.get("construction_status") == "Existing" else ["Property is not reported as existing"]
        energy_reasons = base_reasons + (["Energy meters do not cover Whole Property"] if row.get("metered_areas_energy") != "Whole Property" else [])
        energy_reasons += [f"{k}: {row.get(k, 'missing')}" for k in NYC_ENERGY_FLAGS if row.get(k) != "Ok"]
        water_reasons = base_reasons + (["Water meters do not cover Whole Property"] if row.get("metered_areas_water") != "Whole Property" else [])
        if row.get("alert_water_meter_has_less") != "Ok":
            water_reasons.append("Water meter has less than 12 full calendar months or coverage is unknown")
        quality["energy_publication_status"] = "withheld_incomplete_or_unknown" if energy_reasons else "source_qualified"
        quality["water_publication_status"] = "withheld_incomplete_or_unknown" if water_reasons else "source_qualified"
        missing = []
        for metric, (field, unit, _) in NYC_METRICS.items():
            reasons = water_reasons if metric == "water_use_reported" else energy_reasons
            if reasons:
                withheld.append({"disclosure_location_id": key, "metric": metric, "reasons": reasons})
                missing.append(metric)
                continue
            value = _number(row.get(field))
            if value is None:
                missing.append(metric)
                continue
            est_field = "estimated_values_water" if metric == "water_use_reported" else "estimated_values_energy"
            estimate = row.get(est_field)
            basis = ("publisher_calculated_from_reported_energy" if metric == "reported_ghg_total" else
                     "reported_annual_usage_includes_estimates" if estimate == "Yes" else "reported_annual_usage")
            common = (NYC_SCOPE + f" Publisher {est_field}: {estimate or 'unknown'}. "
                      "Publisher completeness flags passed this metric's gate; underlying bills and meter accuracy are not independently audited. "
                      f"Data-center IT configuration/estimate alert: {row.get('alert_data_center_issue_with', 'unknown')}; this alert concerns IT metrics, not a separately metered datacenter-only total. ")
            note = common + {
                "electricity_consumption": "Annual electricity from grid purchases and onsite renewable systems in kWh, without weather normalization. Energy, not peak power demand.",
                "water_use_reported": "Publisher 'Water Use (All Water Sources)' is the sum of water meters, in kgal (thousand U.S. gallons). It is not independently established consumptive loss, water withdrawal, discharge or cooling-only water.",
                "reported_ghg_total": "Portfolio Manager calculated location-based total from direct fuel and indirect purchased-energy emissions. Source-defined energy boundary; not direct sensor measurement, a complete Scope 1 inventory, market-based accounting or lifecycle emissions.",
            }[metric]
            observations.append(_observation(key, metric, value, unit, year, scope, note, basis, row, field,
                url, sha, "New York City Department of Buildings", quality))
        locations.append({"disclosure_location_id": key, "operator": None,
            "location_label": row.get("property_name") or row["address_1"], "reporting_scope": scope,
            "address": {"street": row["address_1"], "city": row["city"], "state": "NY", "postal_code": row.get("postal_code"), "country": "US"},
            "source_identifiers": {"nyc_property_id": pid, "nyc_bbl": row.get("nyc_borough_block_and_lot"),
                                   "nyc_bin": row.get("nyc_building_identification"), "parent_property_id": row.get("parent_property_id")},
            "scope_note": NYC_SCOPE, "source_property_type": row.get("primary_property_type"),
            "source_property_type_self_selected": row.get("primary_property_type_self"),
            "source_property_uses": row.get("list_of_all_property_use"),
            "data_center_floor_area_sqft": _number(row.get("data_center_gross_floor_area")),
            "number_of_buildings": buildings, "source_quality": quality,
            "missing_metrics": missing, "source_url": url, "source_sha256": sha,
            "raw_source_row": row, "latest_dataset_year": year})
    return {"locations": locations, "observations": observations, "withheld_metrics": withheld, "note": NYC_SCOPE}


def parse_seattle_rows(captured_rows, year):
    locations, observations, withheld, seen = [], [], [], set()
    for row, url, sha in captured_rows:
        pid = str(_integer(row.get("osebuildingid"), "Seattle property ID"))
        if pid in seen:
            raise MunicipalReportingError("Duplicate Seattle reporting property")
        seen.add(pid)
        key = "seattle-benchmarking:" + pid
        if str(row.get("datayear")) != str(year) or row.get("state") != "WA":
            raise MunicipalReportingError("Seattle annual period or state changed")
        if "Data Center" not in [row.get("epapropertytype"), row.get("largestpropertyusetype")]:
            raise MunicipalReportingError("Seattle query returned a property outside the reviewed classification")
        buildings = _integer(row.get("numberofbuildings"), "Seattle building count", 1, 1000)
        scope = "building" if buildings == 1 else "campus"
        if not row.get("address") or not row.get("city"):
            raise MunicipalReportingError("Seattle property lacks its publisher address")
        quality = {k: row.get(k) for k in ("compliancestatus", "complianceissue", "demolished")}
        reasons = []
        if row.get("compliancestatus") != "Compliant" or row.get("complianceissue") != "No Issue":
            reasons.append("City reports noncompliance or a missing/flagged completeness status")
        if row.get("demolished") is not False:
            reasons.append("Property demolition status is not explicitly false")
        missing = []
        for metric, (field, unit, _) in SEATTLE_METRICS.items():
            if reasons:
                missing.append(metric)
                withheld.append({"disclosure_location_id": key, "metric": metric, "reasons": reasons})
                continue
            value = _number(row.get(field))
            if value is None:
                missing.append(metric)
                continue
            note = SEATTLE_SCOPE + " City benchmarking compliance and issue flags passed this release's gate; bill/meter accuracy is not independently audited. "
            note += ("Annual electricity consumed onsite, including grid purchases and onsite renewable systems, in kWh; not demand or weather-normalized energy."
                     if metric == "electricity_consumption" else
                     "Publisher-calculated greenhouse gases from property energy use, in metric tons CO2e. The city states that utility-specific factors from its 2023 Building Emissions Performance Standard are used. This is not direct sensor measurement, lifecycle emissions or a separately established complete Scope 1/2 inventory.")
            basis = "reported_annual_usage" if metric == "electricity_consumption" else "publisher_calculated_from_reported_energy"
            observations.append(_observation(key, metric, value, unit, year, scope, note, basis, row, field,
                url, sha, "City of Seattle Office of Sustainability and Environment", quality))
        locations.append({"disclosure_location_id": key, "operator": None,
            "location_label": row.get("buildingname") or row["address"], "reporting_scope": scope,
            "address": {"street": row["address"], "city": row["city"], "state": "WA", "postal_code": row.get("zipcode"), "country": "US"},
            "source_identifiers": {"seattle_ose_building_id": pid, "king_county_tax_parcel": row.get("taxparcelidentificationnumber")},
            "scope_note": SEATTLE_SCOPE, "source_property_type": row.get("epapropertytype"),
            "source_property_uses": [row.get(k) for k in ("largestpropertyusetype", "secondlargestpropertyusetype", "thirdlargestpropertyusetype")],
            "data_center_floor_area_sqft": _number(row.get("largestpropertyusetypegfa")) if row.get("largestpropertyusetype") == "Data Center" else None,
            "number_of_buildings": buildings, "source_quality": quality,
            "missing_metrics": missing + ["water_use_reported"], "source_url": url, "source_sha256": sha,
            "raw_source_row": row, "latest_dataset_year": year})
    return {"locations": locations, "observations": observations, "withheld_metrics": withheld, "note": SEATTLE_SCOPE}


def _collect(fetcher, now, city):
    nyc = city == "nyc"
    rows, year, capture = _latest_capture(fetcher, base=NYC_BASE if nyc else SEATTLE_BASE,
        dataset=NYC_ID if nyc else SEATTLE_ID, year_field="report_year" if nyc else "datayear",
        id_field="property_id" if nyc else "osebuildingid", selection=NYC_FILTER if nyc else SEATTLE_FILTER,
        fields=NYC_FIELDS if nyc else SEATTLE_FIELDS, metrics=NYC_METRICS if nyc else SEATTLE_METRICS, now=now)
    data = (parse_nyc_rows if nyc else parse_seattle_rows)(rows, year)
    if not data["observations"]:
        raise MunicipalReportingError("No numeric observations pass publisher completeness checks")
    data.update(capture=capture, latest_published_data_year=year, selection_coverage="Latest year, publisher primary/largest-use Data Center classification; no nationwide coverage claim")
    return SourceResult(city + "_benchmarking", ("NYC" if nyc else "Seattle") + " annual building benchmarking disclosures",
        NYC_PAGE if nyc else SEATTLE_PAGE, data, len(data["observations"]), f"{year}-01-01T00:00:00Z", f"{year+1}-01-01T00:00:00Z",
        data_vintage=f"Calendar year {year}; latest numeric reporting year exposed by publisher",
        coverage=f"{len(rows)} reporting properties classified as Data Center by the publisher; whole-property scope and incomplete metrics retained",
        note=NYC_SCOPE if nyc else SEATTLE_SCOPE)


def collect_nyc_benchmarking(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    return _collect(fetcher, now, "nyc")


def collect_seattle_benchmarking(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    return _collect(fetcher, now, "seattle")
