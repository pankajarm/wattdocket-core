"""Reported peak demand with explicit campus versus individual-meter boundaries.

Nebraska values are manually reviewed visible transcriptions of immutable PDF
bytes. The two Google reports were returned to the public file on 2026-11-02;
no hidden or redacted text is extracted. A changed document requires review.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import quote

from .models import Fetcher, SourceResult
from .sources_municipal_reporting import (
    NYC_BASE, NYC_ID, NYC_PAGE, NYC_FILTER, NYC_FIELDS, NYC_ENERGY_FLAGS,
    _capture, _latest_capture, _integer, _number,
)

NE_PAGE = "https://dwee.nebraska.gov/data-center-task-force"
NE_VIEWER = "https://ecmp.nebraska.gov/PublicAccess/index.html?MyQueryID=340"
NE_INDEX = "https://ecmp.nebraska.gov/PublicAccess/api/CustomQuery/KeywordSearch"
NE_DOCUMENT_BASE = "https://ecmp.nebraska.gov/PublicAccess/api/Document/"
NE_QUERY = {"QueryID": 340, "Keywords": [{"ID": 115, "Value": "DCR", "KeywordOperator": "="}],
            "FromDate": None, "ToDate": None, "QueryLimit": 100}
NE_COLUMNS = ("Document Date", "Document Type", "DEQ Description", "DEQ Program ID",
              "DEQ Program", "DEQ Facility Number")
REVIEW_VERSION = "visible-nebraska-peak-2026-11-04-v1"
# Reviewed directly against rendered pages, including each reporting period,
# physical street and the two public-return stamps. SHA pins are indispensable:
# these are source facts, not extraction defaults for an arbitrary later PDF.
NE_REVIEWED = {
    "106518": {"sha256": "8e3ddea4d77198935ef2a045492c020b6e008955357df170b607663c77f12d7b",
        "document_date": "2026-11-02", "pages": 5, "document_number": "20260102836",
        "name": "Fireball Group LLC", "operator": "Google LLC", "street": "14865 Gold Coast Road",
        "city": "Papillion", "postal_code": "68138", "county": "Sarpy", "facility_type": "data_center",
        "reporting_year": 2025, "peak_mw": 254.5, "peak_page": 2, "identity_page": 1,
        "gross_floor_area_sqft": 1140958, "public_return_date": "2026-11-02",
        "source_words": "Max observed demand"},
    "90045": {"sha256": "97fb3e1284c1c82ea64c6b92d868c670621af2eb238bbe9e2b77670de6d92088",
        "document_date": "2026-11-02", "pages": 5, "document_number": "20260102842",
        "name": "Agate LLC", "operator": "Google LLC", "street": "5475 Cloud Court",
        "city": "Lincoln", "postal_code": "68514", "county": "Lancaster", "facility_type": "data_center",
        "reporting_year": 2025, "peak_mw": 52.65, "peak_page": 2, "identity_page": 1,
        "gross_floor_area_sqft": 288530, "public_return_date": "2026-11-02",
        "source_words": "Max observed demand"},
    "114235": {"sha256": "4d3e6aa7be574138f7c035bc50c525608215b70031cbe1b8cb22d9083000ad69",
        "document_date": "2026-09-16", "pages": 2, "document_number": "20260094036",
        "name": "MARA Holdings, Inc. - Kearney", "operator": "MARA Holdings, Inc.",
        "street": "3215 Global Dr. Pl.", "city": "Kearney", "postal_code": "68847", "county": "Buffalo",
        "facility_type": "cryptocurrency_mining", "reporting_year": 2025, "peak_mw": 99,
        "peak_page": 1, "identity_page": 1, "gross_floor_area_sqft": 24121, "land_area_acres": 11,
        "public_return_date": None, "source_words": "99 MW peak demand, 786,878 MWh in 2025"},
}
NE_EXCLUSIONS = {
    "117197": {"sha256": "8f3eb9610037c15a3970f57d90ff933f5cff06c244a8b3e11eff3bf8baaa1f09",
        "reason": "Latest reviewed Westwood Solutions report visibly redacts peak demand; no concealed text or secondary reconstruction is used."},
    "108432": {"sha256": "18f17a401c4c969d06b425ff0719107043e216333bf0233dd2e9335482e3407f",
        "reason": "Meta Sarpy reports annual electricity in MWh, an energy quantity, not a peak-demand reading."},
    "103198": {"sha256": "469c7383d0ec0d429fbba61e3a75f709a1b78e43619702de79c54c4540314d24",
        "reason": "Latest annual-submission amendment clarifies water usage and contains no peak-demand reading; MWh in the original report is annual energy."},
}


class FacilityDemandError(ValueError):
    pass


def _receipt(fetcher, url, sha):
    method = getattr(fetcher, "receipt", None)
    receipt = method(url) if method else None
    if not receipt or receipt.get("sha256") != sha or receipt.get("raw_key") != f"raw/{sha}.gz" or not receipt.get("retrieved_at"):
        raise FacilityDemandError("Demand evidence lacks its matching original immutable capture receipt")
    return receipt


def _nebraska_index(payload, *, now):
    if not isinstance(payload, dict) or payload.get("Truncated") is not False:
        raise FacilityDemandError("Nebraska public index is missing or truncated")
    if [x.get("Heading") for x in payload.get("DisplayColumns", [])] != list(NE_COLUMNS):
        raise FacilityDemandError("Nebraska public index column identities changed")
    rows = payload.get("Data")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise FacilityDemandError("Nebraska public index exceeds its reviewed bound")
    selected, ids = {}, set()
    for row in rows:
        key, cells = row.get("ID"), row.get("DisplayColumnValues")
        if not isinstance(key, str) or not 1 <= len(key) <= 500 or key in ids or not isinstance(cells, list) or len(cells) != 6:
            raise FacilityDemandError("Nebraska document identity is invalid or duplicated")
        ids.add(key)
        values = [cell.get("Value") for cell in cells]
        if values[4] != "DCR" or not re.fullmatch(r"[0-9]{1,8}", str(values[5])):
            raise FacilityDemandError("Nebraska index escaped its DCR facility selection")
        try:
            document_date = datetime.strptime(values[0], "%m/%d/%Y").date()
        except (TypeError, ValueError) as exc:
            raise FacilityDemandError("Nebraska document date is invalid") from exc
        if document_date > now.date():
            raise FacilityDemandError("Nebraska document date is in the future")
        if "ANNUAL DATA CENTER REQUIREMENTS" not in str(values[2]):
            continue
        fid = str(values[5])
        item = {"facility_id": fid, "document_date": document_date.isoformat(), "document_type": values[1],
                "description": values[2], "url": NE_DOCUMENT_BASE + quote(key, safe="") + "/"}
        previous = selected.get(fid)
        if previous and previous["document_date"] == item["document_date"]:
            raise FacilityDemandError("Nebraska has multiple latest annual documents on one date; review required")
        if not previous or item["document_date"] > previous["document_date"]:
            selected[fid] = item
    if not set(NE_REVIEWED) <= set(selected):
        raise FacilityDemandError("A reviewed Nebraska facility disappeared from the public index")
    return selected, len(rows)


def collect_nebraska_facility_demand(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    post = getattr(fetcher, "post_json", None)
    if not post:
        raise FacilityDemandError("Nebraska public discovery requires captured read-only JSON POST support")
    payload = post(NE_INDEX, NE_QUERY)
    selected, indexed_count = _nebraska_index(payload, now=now)
    index_receipt = next((r for r in getattr(fetcher, "receipts", {}).values()
                          if r.get("url") == NE_INDEX and r.get("request_method") == "POST" and r.get("request_body") == NE_QUERY), None)
    if not index_receipt or index_receipt.get("request_method") != "POST" or index_receipt.get("request_body") != NE_QUERY:
        raise FacilityDemandError("Nebraska index receipt lacks its exact public search request")
    _receipt_fields = index_receipt.get("sha256", "")
    if not re.fullmatch(r"[a-f0-9]{64}", _receipt_fields) or index_receipt.get("raw_key") != f"raw/{_receipt_fields}.gz" or not index_receipt.get("retrieved_at"):
        raise FacilityDemandError("Nebraska public index lacks its immutable raw capture receipt")
    sites, demands, excluded, documents = [], [], [], []
    for fid, item in sorted(selected.items()):
        raw = fetcher.get_bytes(item["url"])
        if not raw.startswith(b"%PDF-") or not 1000 <= len(raw) <= 20_000_000:
            raise FacilityDemandError("Nebraska annual report is not a bounded PDF")
        sha = hashlib.sha256(raw).hexdigest()
        receipt = _receipt(fetcher, item["url"], sha)
        documents.append({**item, "sha256": sha, "retrieved_at": receipt["retrieved_at"]})
        reviewed = NE_REVIEWED.get(fid)
        if not reviewed:
            prior = NE_EXCLUSIONS.get(fid, {})
            reason = prior.get("reason") if prior.get("sha256") == sha else "New or changed annual document requires visible-page review before any peak-demand fact can be published."
            excluded.append({"site_id": "ne-dcr:" + fid, "reason": reason, "source_url": item["url"], "source_sha256": sha})
            continue
        if sha != reviewed["sha256"] or item["document_date"] != reviewed["document_date"]:
            raise FacilityDemandError("Reviewed Nebraska peak-demand document changed; visible re-review required: " + fid)
        import pymupdf
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            if len(pdf) != reviewed["pages"]:
                raise FacilityDemandError("Reviewed Nebraska PDF page count changed")
        site_id, year = "ne-dcr:" + fid, reviewed["reporting_year"]
        identity = {"url": item["url"], "sha256": sha, "page": reviewed["identity_page"],
                    "locator": f"Page {reviewed['identity_page']}; physical facility identity/address",
                    "quote": reviewed["street"], "representation": "reviewed_visible_pdf", "review_version": REVIEW_VERSION}
        public_access = {"public_index_url": NE_VIEWER, "document_date": item["document_date"],
                         "returned_to_public_file_at": reviewed["public_return_date"],
                         "stamp_page": 1 if reviewed["public_return_date"] else None,
                         "stamp_text": "RETURN TO PUBLIC FILE" if reviewed["public_return_date"] else None,
                         "redacted_values_used": False}
        sites.append({"site_id": site_id, "name": reviewed["name"], "operator": reviewed["operator"],
            "facility_type": reviewed["facility_type"], "address": {"street": reviewed["street"], "city": reviewed["city"],
                "state": "NE", "postal_code": reviewed["postal_code"], "country": "US"},
            "reporting_scope": "campus", "reporting_boundary": "The physical campus/property identified in this operator's Nebraska annual return; not an individual EPA registration.",
            "source_identifiers": {"nebraska_deq_facility_id": fid, "document_number": reviewed["document_number"]},
            "gross_floor_area_sqft": reviewed["gross_floor_area_sqft"], "identity_evidence": [identity],
            "inventory_record_ids": [], "source_url": item["url"], "source_sha256": sha, "public_access_evidence": public_access})
        note = ("Operator-reported maximum observed electricity demand for its declared 2025 campus/property. "
                "The source does not disclose demand integration interval, peak timestamp/month, individual meter coverage or independent bill audit. "
                "This is an annual peak summary, not an hourly meter series, connection/contract capacity or generator rating. "
                "No number is allocated to an EPA record or to an individual campus building.")
        if reviewed["facility_type"] == "cryptocurrency_mining":
            note += " The report describes ASIC mining equipment; this is a cryptocurrency-mining facility, not an identified AI/cloud data center."
        demands.append({"demand_id": f"{site_id}:{year}:electricity_peak_demand_reported", "site_id": site_id,
            "metric": "electricity_peak_demand_reported", "value": reviewed["peak_mw"], "unit": "MW",
            "reporting_year": year, "period_start": f"{year}-01-01T00:00:00Z", "period_end": f"{year+1}-01-01T00:00:00Z",
            "peak_month": None, "publisher_meter_id": None, "interval_minutes": None,
            "measurement_boundary": "reported_campus_maximum", "reporting_scope": "campus", "whole_facility": True,
            "reporting_basis": "operator_reported_annual_peak", "is_meter_observation": False,
            "source_url": item["url"], "source_sha256": sha, "source_page": reviewed["peak_page"],
            "source_locator": f"Page {reviewed['peak_page']}; {year} peak electrical demand; {reviewed['source_words']}",
            "publisher": "Nebraska Department of Water, Energy, and Environment", "note": note,
            "representation": "reviewed_visible_pdf", "review_version": REVIEW_VERSION,
            "reviewed_document_date": item["document_date"], "public_access_evidence": public_access})
    return SourceResult("nebraska_facility_demand", "Nebraska public annual peak-electricity reports", NE_PAGE,
        {"power_sites": sites, "power_demands": demands, "excluded": excluded, "documents": documents,
         "capture": {"index_url": NE_INDEX, "index_receipt": index_receipt, "indexed_documents": indexed_count,
                     "annual_reporting_facilities": len(selected), "review_version": REVIEW_VERSION},
         "note": "Visible, SHA-pinned public reports; no confidential/redacted recovery, capacity inference or inventory allocation."},
        len(demands), "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        data_vintage="2025 reported campus peaks; public reports reviewed November 2026",
        coverage=f"{len(demands)} reviewed peak-demand reports among {len(selected)} annual reporting facilities in the current public DCR index",
        note="Two Google campus maxima and one explicitly labeled cryptocurrency-mining property maximum. No interval meter feed or comprehensive Nebraska facility census.")


NYC_DEMAND_FIELDS = (*NYC_FIELDS, "annual_maximum_demand_kw", "annual_maximum_demand_mm", "annual_maximum_demand_meter")
NYC_DEMAND_METRICS = {
    "electricity_peak_demand_reported": ("annual_maximum_demand_kw", "kW", "Annual Maximum Demand (kW)"),
    "peak_month": ("annual_maximum_demand_mm", "MM/YYYY", "Annual Maximum Demand (MM/YYYY)"),
    "publisher_meter_id": ("annual_maximum_demand_meter", "text", "Annual Maximum Demand (Meter Name (Meter ID))"),
}
NYC_DEMAND_NOTE = ("Portfolio Manager reports the highest individual electric-meter demand entry across the reporting year, "
    "not the sum of meter peaks or a coincident whole-building/campus maximum. Demand tracking is optional. "
    "Energy completeness flags do not establish complete demand-meter coverage. No demand interval, hourly series or datacenter-only load is inferred.")


def parse_nyc_demand(rows, year):
    sites, demands, excluded, seen = [], [], [], set()
    for row, url, sha in rows:
        pid = str(_integer(row.get("property_id"), "NYC demand property ID"))
        if pid in seen:
            raise FacilityDemandError("Repeated NYC demand property")
        seen.add(pid)
        site_id = "nyc:" + pid
        if str(row.get("report_year")) != str(year) or str(row.get("year_ending", ""))[:10] != f"{year}-12-31":
            raise FacilityDemandError("NYC demand report period changed")
        if "Data Center" not in [row.get(k) for k in ("primary_property_type_self", "primary_property_type", "largest_property_use_type")]:
            raise FacilityDemandError("NYC demand property is outside the reviewed Data Center selection")
        if not row.get("address_1") or not row.get("city"):
            raise FacilityDemandError("NYC demand property lacks physical-address context")
        _integer(row.get("number_of_buildings"), "NYC demand building count", 1, 1000)
        value = _number(row.get("annual_maximum_demand_kw"))
        reasons = []
        if value is None:
            reasons.append("Annual maximum demand is not published")
        elif value == 0:
            reasons.append("Publisher reports zero demand but optional demand-meter coverage is unverified; zero is retained as source evidence, not claimed as zero facility power")
        if row.get("construction_status") != "Existing" or row.get("metered_areas_energy") != "Whole Property" or any(row.get(f) != "Ok" for f in NYC_ENERGY_FLAGS):
            reasons.append("Publisher energy completeness/status flags are incomplete or unknown; demand reading withheld conservatively")
        month, meter = row.get("annual_maximum_demand_mm"), row.get("annual_maximum_demand_meter")
        if value and (not re.fullmatch(r"(?:0[1-9]|1[0-2])/" + str(year), str(month)) or
                      not isinstance(meter, str) or not re.fullmatch(r".+ \([0-9]+\)", meter)):
            raise FacilityDemandError("NYC positive demand lacks its reporting-year month or publisher meter identity")
        if reasons:
            excluded.append({"site_id": site_id, "reasons": reasons, "raw_reported_demand_kw": row.get("annual_maximum_demand_kw"),
                "source_url": url, "source_sha256": sha, "raw_source_row": row})
            continue
        meter_id = re.search(r"\(([0-9]+)\)$", meter).group(1)
        evidence_fields = {k: row.get(k) for k in ("property_id", "address_1", "city", "nyc_borough_block_and_lot", "nyc_building_identification")}
        sites.append({"site_id": site_id, "name": row.get("property_name") or row["address_1"], "operator": None,
            "address": {"street": row["address_1"], "city": row["city"], "state": "NY", "postal_code": row.get("postal_code"), "country": "US"},
            "reporting_scope": "meter", "reporting_boundary": NYC_DEMAND_NOTE,
            "source_identifiers": evidence_fields, "related_annual_location_id": "nyc-ll84:" + pid,
            "identity_evidence": [{"url": url, "sha256": sha, "locator": "Exact publisher property ID and physical address fields",
                                   "source_fields": evidence_fields, "representation": "publisher_json"}],
            "inventory_record_ids": [], "source_url": url, "source_sha256": sha, "raw_source_row": row})
        demands.append({"demand_id": f"{site_id}:{year}:electricity_peak_demand_reported", "site_id": site_id,
            "metric": "electricity_peak_demand_reported", "value": value, "unit": "kW", "reporting_year": year,
            "period_start": f"{year}-01-01T00:00:00Z", "period_end": f"{year+1}-01-01T00:00:00Z",
            "peak_month": f"{year}-{month[:2]}", "publisher_meter_id": meter_id, "publisher_meter_name_id": meter,
            "measurement_boundary": "largest_individual_meter_entry", "reporting_scope": "meter", "whole_facility": False,
            "interval_minutes": None, "reporting_basis": "publisher_reported_individual_meter_annual_maximum",
            "is_meter_observation": False, "source_url": url, "source_sha256": sha,
            "source_locator": f"Property {pid}, calendar year {year}, annual_maximum_demand_kw; annual_maximum_demand_mm; annual_maximum_demand_meter",
            "publisher": "New York City Department of Buildings", "note": NYC_DEMAND_NOTE,
            "energy_estimates_flag": row.get("estimated_values_energy"), "demand_coverage_verified": False})
    return {"power_sites": sites, "power_demands": demands, "excluded": excluded, "note": NYC_DEMAND_NOTE}


def collect_nyc_facility_demand(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    rows, year, capture = _latest_capture(fetcher, base=NYC_BASE, dataset=NYC_ID,
        year_field="report_year", id_field="property_id", selection=NYC_FILTER, fields=NYC_DEMAND_FIELDS,
        metrics=NYC_DEMAND_METRICS, now=now)
    metadata, meta_sha = _capture(fetcher, capture["metadata_url"])
    definition = next((str(c.get("description", "")).lower() for c in metadata.get("columns", [])
                       if c.get("fieldName") == "annual_maximum_demand_kw"), "")
    if meta_sha != capture["metadata_sha256"] or not all(text in definition for text in
            ("highest demand value", "not a cumulative number", "one meter")):
        raise FacilityDemandError("NYC individual-meter demand definition changed during capture or needs review")
    data = parse_nyc_demand(rows, year)
    data["capture"] = capture
    if not data["power_demands"]:
        raise FacilityDemandError("No positive NYC demand value passes identity, period and quality checks")
    return SourceResult("nyc_facility_demand", "NYC reported individual-meter annual maximum demand", NYC_PAGE,
        data, len(data["power_demands"]), f"{year}-01-01T00:00:00Z", f"{year+1}-01-01T00:00:00Z",
        data_vintage=f"Calendar year {year}; largest reported individual-meter demand entry",
        coverage=f"{len(data['power_demands'])} positive meter maxima among {len(rows)} publisher-classified Data Center properties",
        note=NYC_DEMAND_NOTE)
