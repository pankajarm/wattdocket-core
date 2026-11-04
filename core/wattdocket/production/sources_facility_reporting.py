"""Selected public building-energy and GHGRP disclosures, with native units.

These are reporting entities, not automatic inventory joins. Nulls are missing,
annual energy is not demand, and regulatory combustion totals are not Scope 2.
The caller archives each response and owns retry/freshness/publication policy.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

from .models import Fetcher, SourceResult

MA_PAGE = "https://www.mass.gov/info-details/large-building-energy-reporting-results"
MA_APP = "https://www.arcgis.com/sharing/rest/content/items/47c49604b161458aac09dc472b0576ae/data?f=pjson"
MA_LAYER = ("https://services1.arcgis.com/7iJyYTjCtKsZS1LR/arcgis/rest/services/"
            "2026_10_28_LBER_Annual_Report_Data/FeatureServer/0")
MA_YEAR = 2025
MA_ELECTRICITY = "Total_Electricity_Use_Purchased_From_Grid_kBtu"
MA_GHG = "F2025_Total_GHG_Emissions_Metric_Tons_COe"
# Bounded, reviewed reporting-entity selection. Names/addresses are returned by
# the publisher, never substituted with an operator guess. These keys do not
# assert that an EPA record has the same physical or reporting boundary.
MA_UBIDS = (
    "87JC9WGC+M3Q-24-19-25-20",  # 70 Inner Belt Rd, Somerville
    "87JC9PVM+6RG-14-14-14-14",  # 115 2Nd Ave, Waltham
    "87JC7CJ9+9W9-12-20-14-20",  # 600 Friberg Pkwy, Westborough
    "87JC7826+GX5-21-26-18-28",  # 8 Centennial Dr, Shrewsbury (not Grafton)
    "87JC8CG5+H5J-18-18-15-17",  # PHS Partners Data Center West
    "87JCHQ2M+7VV-32-27-31-23",  # 41 Alexander Rd, Billerica
    "87JC9C27+XHM-13-17-12-17",  # 250 Locke Dr, Marlborough
    "87J9692R+XVR-18-18-17-18",  # MGHPCC; 2025 metrics currently missing
    "87JC9PWJ+QM4-20-12-19-10",  # 600 Winter St; 2025 metrics currently missing
)
MA_NOTE = ("Selected Massachusetts LBER building reporting entities. Annual purchased-grid electricity "
           "is reported in kBtu, an energy unit, not MW demand. The state publishes calculated total "
           "GHG emissions using reported energy, not directly measured emissions or a separately "
           "identified Scope 1/2 inventory. Data gaps and incomplete reports remain possible. "
           "Null fields are omitted as observations. No water-use field is supplied by this dataset. "
           "No value is allocated across buildings or to an EPA inventory record.")

EPA_BASE = "https://data.epa.gov/efservice/"
GHGRP_PAGE = "https://www.epa.gov/ghgreporting/ghg-reporting-program-data-sets"
GHGRP_DIM_URL = EPA_BASE + "PUB_DIM_FACILITY/NAICS_CODE/518210/ROWS/0:999/JSON"
GHGRP_GAS_URL = EPA_BASE + "PUB_DIM_GHG/JSON"
GHGRP_SUBPART_URL = EPA_BASE + "PUB_DIM_SUBPART/ROWS/0:99/JSON"
GHGRP_NOTE = ("GHGRP reported direct stationary-combustion emissions for regulatory facilities "
              "classified by the publisher under NAICS 518210. Only Subpart C rows are accepted. "
              "The total sums the publisher's gas-level CO2-equivalent quantities, excluding "
              "biogenic CO2. Reporting may use calculation methods; this is not a continuous "
              "measurement, electricity Scope 2, lifecycle footprint, or complete operator footprint. "
              "Multiple GHGRP facilities sharing one FRS identifier remain separate reporting entities. "
              "A reported address can be a corporate contact address, not a physical site.")


class FacilityReportingError(ValueError):
    """A source cannot be safely interpreted under its reviewed contract."""


def _json(fetcher: Fetcher, url: str):
    raw = fetcher.get_bytes(url)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise FacilityReportingError("Publisher did not return valid JSON: " + url) from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise FacilityReportingError("Publisher returned an API error: " + str(payload["error"]))
    return payload, hashlib.sha256(raw).hexdigest()


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise FacilityReportingError("Reported quantity is nonnumeric, nonfinite or negative")
    return value


def _id(value, name):
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,15}", str(value or "")):
        raise FacilityReportingError("Invalid publisher " + name)
    return str(value)


def _year(value, now):
    if isinstance(value, bool) or not isinstance(value, int) or not 2010 <= value < now.year:
        raise FacilityReportingError("Invalid or incomplete annual reporting period")
    return value


def _observation(key, metric, value, unit, year, scope, basis, note, url, sha, publisher, **extra):
    return {"observation_id": f"{key}:{year}:{metric}", "disclosure_location_id": key,
            "metric": metric, "value": value, "unit": unit, "data_year": year,
            "period_start": f"{year}-01-01T00:00:00Z", "period_end": f"{year + 1}-01-01T00:00:00Z",
            "reporting_scope": scope, "reporting_basis": basis, "methodology_note": note,
            "source_url": url, "source_sha256": sha, "publisher": publisher,
            "facility_binding_verified": False, **extra}


def ma_location_id(ubid: str) -> str:
    return "ma-lber:" + ubid.lower().replace("+", "-")


def _walk_urls(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "url" and isinstance(child, str):
                yield child
            yield from _walk_urls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_urls(child)


def parse_massachusetts(rows: list[dict], *, source_url: str, source_sha256: str,
                        now: datetime) -> dict:
    """Preserve the UBID boundary and raw cells; never fill missing quantities."""
    _year(MA_YEAR, now)
    locations, observations, seen = [], [], set()
    for row in rows:
        ubid = row.get("UBID")
        if ubid not in MA_UBIDS or ubid in seen:
            raise FacilityReportingError("Unexpected or duplicate Massachusetts UBID")
        seen.add(ubid)
        if not row.get("Street_Address") or not row.get("City"):
            raise FacilityReportingError("Selected building lost its published address")
        if row.get("Parent_Child_Status") not in (None, "", "Standalone"):
            raise FacilityReportingError("Selected building became a parent/child reporting boundary; review required")
        key = ma_location_id(ubid)
        name = row.get("Property_Name") or row["Street_Address"]
        missing = [metric for metric, field in [("electricity_consumption", MA_ELECTRICITY),
                                                ("reported_ghg_total", MA_GHG)] if row.get(field) is None]
        locations.append({"disclosure_location_id": key, "operator": None,
            "location_label": f"{name} · {row['City']}, MA", "reporting_scope": "building",
            "address": {"street": row["Street_Address"], "city": row["City"], "state": "MA",
                        "postal_code": row.get("Zip_Code"), "country": "US"},
            "source_identifiers": {"ma_ubid": ubid}, "source_url": source_url,
            "source_sha256": source_sha256, "source_property_type": row.get("Property_Type"),
            "scope_note": MA_NOTE, "latest_dataset_year": MA_YEAR,
            "missing_metrics": missing + ["water_withdrawal", "water_consumption", "water_discharge"],
            "raw_source_row": row})
        for metric, field, unit, basis in (
            ("electricity_consumption", MA_ELECTRICITY, "kBtu", "reported_energy"),
            ("reported_ghg_total", MA_GHG, "metric tons CO2e", "publisher_calculated_from_reported_energy"),
        ):
            if field not in row:
                raise FacilityReportingError("Massachusetts source row lost quantity field " + field)
            value = _number(row[field])
            if value is None:
                continue
            observations.append(_observation(key, metric, value, unit, MA_YEAR, "building", basis,
                MA_NOTE, source_url, source_sha256, "Massachusetts Department of Energy Resources",
                source_field=field, source_page=f"UBID {ubid}; field {field}",
                source_quote=json.dumps({"UBID": ubid, field: row[field]}, ensure_ascii=False),
                coverage="Purchased-grid electricity only" if metric == "electricity_consumption" else
                         "Publisher-calculated total building energy emissions; source-defined boundary"))
    if seen != set(MA_UBIDS):
        raise FacilityReportingError("Selected Massachusetts building IDs missing: " + ", ".join(sorted(set(MA_UBIDS) - seen)))
    return {"locations": locations, "observations": observations, "selected_ubids": list(MA_UBIDS),
            "data_year": MA_YEAR, "note": MA_NOTE}


def collect_massachusetts_reporting(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    app, app_sha = _json(fetcher, MA_APP)
    urls = set(_walk_urls(app))
    if not ({MA_LAYER, MA_LAYER.rsplit("/", 1)[0]} & urls):
        raise FacilityReportingError("Massachusetts live application no longer references the reviewed annual layer")
    # Fail closed when a newer annual dataset appears; do not silently keep
    # selecting the previous year's hard-coded columns after publisher changes.
    layer_years = [int(y) for url in urls for y in re.findall(r"/(20\d\d)(?:_\d\d_\d\d_LBER)?_Annual_Report_Data/", url)]
    if not layer_years or max(layer_years) != MA_YEAR + 1:
        raise FacilityReportingError("Massachusetts latest annual edition changed; review schema/year before release")
    meta, meta_sha = _json(fetcher, MA_LAYER + "?f=pjson")
    fields = {x.get("name"): x.get("alias") for x in meta.get("fields", [])}
    if fields.get(MA_ELECTRICITY) != "Total Electricity Use Purchased From Grid kBtu" or fields.get(MA_GHG) != "2025 Total GHG Emissions Metric Tons COe":
        raise FacilityReportingError("Massachusetts energy/emissions units or year changed")
    where = "UBID IN (" + ",".join("'" + x + "'" for x in MA_UBIDS) + ")"
    url = MA_LAYER + "/query?" + urlencode({"f": "json", "where": where, "outFields": "*",
        "returnGeometry": "false", "orderByFields": "UBID", "resultRecordCount": 100})
    payload, sha = _json(fetcher, url)
    if payload.get("exceededTransferLimit") or not isinstance(payload.get("features"), list):
        raise FacilityReportingError("Massachusetts query truncated or malformed")
    rows = [x["attributes"] for x in payload["features"]]
    data = parse_massachusetts(rows, source_url=url, source_sha256=sha, now=now)
    data.update(discovery_url=MA_APP, discovery_sha256=app_sha, schema_sha256=meta_sha,
                dataset_url=MA_LAYER, source_page=MA_PAGE)
    return SourceResult("massachusetts_energy", "Massachusetts selected building energy disclosures", MA_PAGE,
        data, len(data["observations"]), f"{MA_YEAR}-01-01T00:00:00Z", f"{MA_YEAR + 1}-01-01T00:00:00Z",
        data_vintage=f"Calendar year {MA_YEAR}; 2026 annual dataset", coverage=f"{len(rows)} selected UBID building records; not a statewide datacenter census",
        note=MA_NOTE)


def parse_ghgrp(dimension: list[dict], emissions: dict[str, tuple[list[dict], str, str]], *,
                dimension_sha256: str, now: datetime) -> dict:
    locations, observations = [], []
    by_facility = {}
    for row in dimension:
        fid = _id(row.get("facility_id"), "GHGRP facility ID")
        year = _year(row.get("year"), now)
        if row.get("naics_code") != "518210" or row.get("facility_types") != "Direct Emitter" or row.get("reported_subparts") != "C":
            raise FacilityReportingError("GHGRP reporting classification/boundary changed; review required")
        if year in by_facility.setdefault(fid, {}):
            raise FacilityReportingError("Duplicate GHGRP facility-year dimension")
        _id(row.get("frs_id"), "FRS ID")
        by_facility[fid][year] = row
    if not 1 <= len(by_facility) <= 50 or set(emissions) != set(by_facility):
        raise FacilityReportingError("GHGRP capture does not match selected facility IDs")
    frs_groups = {}
    for fid, years in by_facility.items():
        latest = years[max(years)]
        frs_groups.setdefault(latest["frs_id"], []).append(fid)
    for fid, years in sorted(by_facility.items()):
        latest = years[max(years)]
        key = "ghgrp:" + fid
        if len({str(r["frs_id"]) for r in years.values()}) != 1:
            raise FacilityReportingError("GHGRP facility FRS relationship changed across annual rows")
        locations.append({"disclosure_location_id": key, "operator": latest.get("parent_company"),
            "location_label": latest["facility_name"], "reporting_scope": "regulatory_facility",
            "address": {"street": latest.get("address1"), "city": latest.get("city"), "state": latest.get("state"),
                        "postal_code": latest.get("zip"), "country": "US"},
            "source_identifiers": {"ghgrp": fid, "frs": str(latest["frs_id"]), "eggrt": str(latest.get("eggrt_facility_id"))},
            "source_url": GHGRP_DIM_URL, "source_sha256": dimension_sha256,
            "scope_note": GHGRP_NOTE, "shared_frs_reporting_entities": frs_groups[latest["frs_id"]],
            "raw_source_rows": list(years.values())})
        rows, url, sha = emissions[fid]
        grouped, seen = {}, set()
        for row in rows:
            if str(row.get("facility_id")) != fid or row.get("sub_part_id") != 1:
                raise FacilityReportingError("GHGRP returned another facility or an unreviewed subpart")
            year = _year(row.get("year"), now)
            gas = row.get("gas_id")
            if gas not in {1, 2, 3, 8} or isinstance(gas, bool):
                raise FacilityReportingError("GHGRP combustion gas definition changed")
            if year not in years or (year, gas) in seen:
                raise FacilityReportingError("GHGRP emission rows lack their own annual identity or repeat a gas")
            seen.add((year, gas))
            value = _number(row.get("co2e_emission"))
            if value is None:
                raise FacilityReportingError("GHGRP gas quantity is missing")
            if gas != 8:
                grouped.setdefault(year, []).append(row)
        if set(grouped) != set(years):
            raise FacilityReportingError("GHGRP annual emissions incomplete")
        for year, components in sorted(grouped.items()):
            # For this reviewed combustion-only lane, missing CO2/CH4/N2O
            # must not silently become a deceptively low annual total.
            if {x["gas_id"] for x in components} != {1, 2, 3}:
                raise FacilityReportingError("GHGRP combustion gas components incomplete")
            total = float(sum((Decimal(str(x["co2e_emission"])) for x in components), Decimal(0)))
            observations.append(_observation(key, "reported_ghg_total", total, "metric tons CO2e", year,
                "regulatory_facility", "sum_of_publisher_reported_co2e_components", GHGRP_NOTE, url, sha,
                "U.S. Environmental Protection Agency Greenhouse Gas Reporting Program",
                source_page=f"GHGRP facility {fid}; year {year}; Subpart C; gas IDs 1, 2, 3",
                source_field="co2e_emission", source_quote=json.dumps(components, sort_keys=True),
                source_components=components, coverage="Direct stationary fuel combustion, Subpart C; excludes electricity Scope 2 and biogenic CO2"))
    return {"locations": locations, "observations": observations, "note": GHGRP_NOTE}


def collect_ghgrp_reporting(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    gases, gas_sha = _json(fetcher, GHGRP_GAS_URL)
    subparts, subpart_sha = _json(fetcher, GHGRP_SUBPART_URL)
    if not isinstance(gases, list) or {x.get("gas_id"): x.get("gas_code") for x in gases
            if x.get("gas_id") in {1, 2, 3, 8}} != {1: "CO2", 2: "CH4", 3: "N2O", 8: "BIOCO2"}:
        raise FacilityReportingError("GHGRP gas identifiers changed")
    if not isinstance(subparts, list) or [x.get("subpart_name") for x in subparts
                                        if x.get("subpart_id") == 1] != ["C"]:
        raise FacilityReportingError("GHGRP Subpart C identifier changed")
    dimension, sha = _json(fetcher, GHGRP_DIM_URL)
    if not isinstance(dimension, list) or not 1 <= len(dimension) < 1000:
        raise FacilityReportingError("GHGRP dimension response empty, malformed or at capture limit")
    # Publisher NAICS query is bounded; new rows are accepted only if they keep
    # this combustion-only source contract. Other classifications require review.
    ids = sorted({_id(r.get("facility_id"), "facility ID") for r in dimension})
    if len(ids) > 50:
        raise FacilityReportingError("GHGRP facility count exceeds reviewed capture bound")
    def capture(fid):
        url = EPA_BASE + f"PUB_FACTS_SUBP_GHG_EMISSION/FACILITY_ID/{fid}/ROWS/0:999/JSON"
        rows, row_sha = _json(fetcher, url)
        if not isinstance(rows, list) or not 1 <= len(rows) < 1000:
            raise FacilityReportingError("GHGRP emissions response empty, malformed or truncated")
        return fid, (rows, url, row_sha)
    with ThreadPoolExecutor(max_workers=3) as pool:
        emissions = dict(pool.map(capture, ids))
    data = parse_ghgrp(dimension, emissions, dimension_sha256=sha, now=now)
    years = sorted({r["data_year"] for r in data["observations"]})
    data.update(dimension_url=GHGRP_DIM_URL, dimension_sha256=sha, latest_published_data_year=max(years),
                gas_dictionary_sha256=gas_sha, subpart_dictionary_sha256=subpart_sha)
    return SourceResult("ghgrp", "EPA GHGRP selected data-processing combustion reports", GHGRP_PAGE,
        data, len(data["observations"]), f"{min(years)}-01-01T00:00:00Z", f"{max(years) + 1}-01-01T00:00:00Z",
        data_vintage=f"Public GHGRP reporting years {min(years)}–{max(years)}",
        coverage=f"{len(ids)} regulatory reporting entities under NAICS 518210, Subpart C only; not all datacenters",
        note=GHGRP_NOTE)
