"""Independent publication gate rejects normalized facts that drift from cells."""
import copy
import json
from pathlib import Path

import pytest

from wattdocket.production.disclosures import build_disclosures
from wattdocket.production.municipal_quality import MunicipalEvidenceError, validate_municipal_observation
from wattdocket.production.quality import RecordEvidenceError, validate_disclosure_publication
from wattdocket.production.sources_municipal_reporting import parse_nyc_rows, parse_seattle_rows

ROOT = Path(__file__).parent / "fixtures/municipal_reporting"


def published(city):
    captures = json.loads((ROOT / f"{city}_benchmarking.json").read_text())
    rows = [(r, url, entry["receipt"]["sha256"]) for url, entry in captures.items() for r in entry["payload"]]
    data = parse_nyc_rows(rows, 2024) if city == "nyc" else parse_seattle_rows(rows, 2025)
    state = {"result": {"data": data}, "receipts": [entry["receipt"] for entry in captures.values()],
             "status": "current", "last_success_at": "2026-11-04T22:00:00Z"}
    payload, _, coverage, _ = build_disclosures({city + "_benchmarking": state}, [], "municipal-test", "2026-11-04T22:00:00Z")
    return payload, coverage


@pytest.mark.parametrize("city,count", [("nyc", 161), ("seattle", 22)])
def test_all_real_qualified_observations_match_native_cells(city, count):
    payload, coverage = published(city)
    assert payload["observation_count"] == count
    validate_disclosure_publication(payload, {}, coverage)
    for location in payload["locations"]:
        for row in location["observations"]:
            validate_municipal_observation(location, row)


def chosen(city="nyc", metric="electricity_consumption"):
    payload, coverage = published(city)
    location = next(l for l in payload["locations"] if any(o["metric"] == metric for o in l["observations"]))
    row = next(o for o in location["observations"] if o["metric"] == metric)
    return payload, coverage, location, row


@pytest.mark.parametrize("city", ["nyc", "seattle"])
@pytest.mark.parametrize("mutation", ["value", "wrong_energy_unit", "wrong_field", "year", "scope", "property", "meter_claim", "raw_missing", "basis", "dataset"])
def test_emitted_numeric_identity_and_method_mutations_fail_closed(city, mutation):
    payload, coverage, location, row = chosen(city)
    if mutation == "value": row["value"] += 123456
    elif mutation == "wrong_energy_unit": row["unit"] = "MWh"  # energy, but not this native cell's unit
    elif mutation == "wrong_field": row["source_field"] = "some_other_quantity"
    elif mutation == "year":
        row.update(data_year=2023, period_start="2023-01-01T00:00:00Z", period_end="2024-01-01T00:00:00Z")
    elif mutation == "scope": location["reporting_scope"] = row["reporting_scope"] = "campus"
    elif mutation == "property": location["raw_source_row"]["property_id" if city == "nyc" else "osebuildingid"] = "999999"
    elif mutation == "meter_claim": row["is_meter_observation"] = True
    elif mutation == "raw_missing": location.pop("raw_source_row")
    elif mutation == "basis": row["reporting_basis"] = "meter_measured"
    else: row["source_url"] = location["source_url"] = "https://other.gov/resource/wrong.json"
    with pytest.raises((RecordEvidenceError, MunicipalEvidenceError)):
        validate_disclosure_publication(payload, {}, coverage)


@pytest.mark.parametrize("flag", ["alert_energy_meter_has_less", "alert_energy_meter_has_gaps", "alert_energy_meter_has",
                                  "alert_energy_no_meters", "alert_energy_meter_has_single", "metered_areas_energy", "construction_status"])
def test_nyc_flag_mutation_cannot_publish_an_incomplete_annual_value(flag):
    _, _, location, row = chosen()
    location["raw_source_row"][flag] = "Possible Issue"
    # Alter the emitted badges too: the independent gate still requires the
    # original publisher's qualified status, not agreement between two copies.
    location["source_quality"][flag] = row["source_quality"][flag] = "Possible Issue"
    with pytest.raises(MunicipalEvidenceError): validate_municipal_observation(location, row)


@pytest.mark.parametrize("flag", ["alert_water_meter_has_less", "metered_areas_water"])
def test_nyc_water_partial_coverage_cannot_pass_as_property_use(flag):
    _, _, location, row = chosen(metric="water_use_reported")
    location["raw_source_row"][flag] = "Possible Issue"
    with pytest.raises(MunicipalEvidenceError): validate_municipal_observation(location, row)


@pytest.mark.parametrize("metric", ["electricity_consumption", "water_use_reported", "reported_ghg_total"])
def test_nyc_estimate_flag_must_keep_its_basis_and_limitation(metric):
    _, _, location, row = chosen(metric=metric)
    flag = "estimated_values_water" if metric == "water_use_reported" else "estimated_values_energy"
    location["raw_source_row"][flag] = "Yes"
    location["source_quality"][flag] = row["source_quality"][flag] = "Yes"
    with pytest.raises(MunicipalEvidenceError): validate_municipal_observation(location, row)
    row["methodology_note"] = row["methodology_note"].replace(f"{flag}: No.", f"{flag}: Yes.")
    row["reporting_basis"] = "publisher_calculated_from_reported_energy" if metric == "reported_ghg_total" else "reported_annual_usage_includes_estimates"
    validate_municipal_observation(location, row)


@pytest.mark.parametrize("flag,value", [("compliancestatus", "Noncompliant"), ("complianceissue", "Missing Data"), ("demolished", True)])
def test_seattle_compliance_cannot_be_promoted_to_valid_numbers(flag, value):
    _, _, location, row = chosen("seattle")
    location["raw_source_row"][flag] = value
    location["source_quality"][flag] = row["source_quality"][flag] = value
    with pytest.raises(MunicipalEvidenceError): validate_municipal_observation(location, row)


def test_decimal_native_value_is_not_rounded_or_relabelled_water_consumption():
    _, _, location, row = chosen(metric="water_use_reported")
    assert row["value"] == 18566.6 and row["unit"] == "kgal"
    validate_municipal_observation(location, row)
    row["metric"] = "water_consumption"
    with pytest.raises(MunicipalEvidenceError): validate_municipal_observation(location, row)


def test_quality_badge_and_source_identity_cannot_bypass_native_checks():
    _, _, location, row = chosen()
    row["source_quality"] = copy.deepcopy(row["source_quality"])
    row["source_quality"]["estimated_values_energy"] = "Yes"
    with pytest.raises(MunicipalEvidenceError): validate_municipal_observation(location, row)
    location["source_id"] = row["source_id"] = "other_source"
    with pytest.raises(MunicipalEvidenceError): validate_municipal_observation(location, row)
