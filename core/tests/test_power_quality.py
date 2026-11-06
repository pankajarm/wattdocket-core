"""Independent publication gate rejects plausible but unsupported peak claims."""
import json
from pathlib import Path

import pytest

from wattdocket.production.power_quality import PowerEvidenceError, validate_demand

FIXTURE = Path(__file__).parent / "fixtures/facility_demand/power-publication.json"


def published(key="ne-dcr:106518"):
    data = json.loads(FIXTURE.read_text())
    return (next(row for row in data["demands"] if row["site_id"] == key),
            next(row for row in data["sites"] if row["site_id"] == key))


@pytest.mark.parametrize("key", ["ne-dcr:106518", "ne-dcr:90045", "ne-dcr:114235", "nyc:21355509"])
def test_captured_publication_contract_passes_independent_evidence_check(key):
    item, site = published(key)
    validate_demand(item, site)


@pytest.mark.parametrize("mutation", ["value", "unit", "year", "period", "hash", "street", "city", "operator", "site_id",
    "public_stamp", "public_date", "redacted", "page", "receipt", "allocation", "whole", "month", "interval", "source"])
def test_nebraska_cannot_swap_property_or_relabel_capacity_time_or_value(mutation):
    item, site = published()
    if mutation == "value": item["value"] = 300
    elif mutation == "unit": item["unit"] = "kW"
    elif mutation == "year": item["reporting_year"] = 2026
    elif mutation == "period": item["period_start"] = "2026-01-01T00:00:00Z"
    elif mutation == "hash":
        item["source_sha256"] = site["source_sha256"] = "0" * 64
        item["source_receipt"].update(sha256="0" * 64, raw_key="raw/" + "0" * 64 + ".gz")
    elif mutation == "street": site["address"]["street"] = "8425 N 114th Street"
    elif mutation == "city": site["address"]["city"] = "Omaha"
    elif mutation == "operator": site["operator"] = "Different owner"
    elif mutation == "site_id": item["site_id"] = site["site_id"] = "ne-dcr:90045"
    elif mutation == "public_stamp":
        item["public_access_evidence"]["stamp_text"] = site["public_access_evidence"]["stamp_text"] = None
    elif mutation == "public_date":
        item["public_access_evidence"]["returned_to_public_file_at"] = site["public_access_evidence"]["returned_to_public_file_at"] = "2026-10-01"
    elif mutation == "redacted":
        item["public_access_evidence"]["redacted_values_used"] = site["public_access_evidence"]["redacted_values_used"] = True
    elif mutation == "page": item["source_page"] = 4
    elif mutation == "receipt": item["retrieved_at"] = "2026-11-04T23:59:59Z"
    elif mutation == "allocation": site["inventory_record_ids"] = ["echo:110064734202"]
    elif mutation == "whole": item["whole_facility"] = False
    elif mutation == "month": item["peak_month"] = "2025-07"
    elif mutation == "interval": item["interval_minutes"] = 60
    else: item["source_id"] = site["source_id"] = "unknown_source"
    with pytest.raises(PowerEvidenceError): validate_demand(item, site)


def test_crypto_peak_cannot_be_presented_as_cloud_data_center():
    item, site = published("ne-dcr:114235")
    site["facility_type"] = "data_center"
    with pytest.raises(PowerEvidenceError, match="type changed"):
        validate_demand(item, site)


@pytest.mark.parametrize("mutation", ["value", "unit", "month", "meter", "whole", "coverage", "year", "street", "operator", "source_field_id", "estimate"])
def test_nyc_single_meter_peak_must_match_its_native_source_and_boundary(mutation):
    item, site = published("nyc:21355509")
    if mutation == "value": item["value"] = 1200
    elif mutation == "unit": item["unit"] = "MW"
    elif mutation == "month": item["peak_month"] = "2024-07"
    elif mutation == "meter": item["publisher_meter_id"] = "133396019"
    elif mutation == "whole": item["whole_facility"] = True
    elif mutation == "coverage": item["demand_coverage_verified"] = True
    elif mutation == "year": site["raw_source_row"]["report_year"] = "2023"
    elif mutation == "street": site["address"]["street"] = "11 Metrotech Ctr"
    elif mutation == "operator": site["operator"] = "Google"
    elif mutation == "source_field_id": site["source_identifiers"]["nyc_building_identification"] = "unrelated"
    else: item["energy_estimates_flag"] = "Yes"
    with pytest.raises(PowerEvidenceError): validate_demand(item, site)


@pytest.mark.parametrize("field", ["alert_energy_meter_has_less", "alert_energy_meter_has_gaps", "alert_energy_meter_has",
    "alert_energy_no_meters", "alert_energy_meter_has_single", "metered_areas_energy", "construction_status"])
def test_positive_nyc_demand_with_incomplete_native_coverage_is_rejected(field):
    item, site = published("nyc:21355509")
    site["raw_source_row"][field] = "Unknown"
    with pytest.raises(PowerEvidenceError, match="flags"):
        validate_demand(item, site)
