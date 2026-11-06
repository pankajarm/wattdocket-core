"""The public-release verifier must independently reject modified source cells."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from wattdocket.production.disclosures import build_disclosures
from wattdocket.production.sources_municipal_reporting import parse_nyc_rows, parse_seattle_rows

FIXTURES = Path(__file__).parent / "fixtures/municipal_reporting"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify_production_release.py"
spec = importlib.util.spec_from_file_location("municipal_independent_verifier", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.fixture
def published():
    states = {}
    for city, year, parser in [("nyc", 2024, parse_nyc_rows), ("seattle", 2025, parse_seattle_rows)]:
        saved = json.loads((FIXTURES / f"{city}_benchmarking.json").read_text())
        rows = [(row, url, item["receipt"]["sha256"]) for url, item in saved.items() for row in item["payload"]]
        data = parser(rows, year)
        for location in data["locations"]:
            location["latest_dataset_year"] = year
        states[city + "_benchmarking"] = {"result": {"data": data}, "status": "current",
            "last_success_at": "2026-11-04T22:00:00Z", "receipts": [item["receipt"] for item in saved.values()]}
    payload, _, coverage, _ = build_disclosures(states, [], "fixture", "2026-11-04T22:00:00Z")
    return {"facility-observations.json": payload, "cards.json": {}, "operators.json": {}}, coverage


def location(published, city="nyc"):
    key = "nyc-ll84:2614608" if city == "nyc" else "seattle-benchmarking:131"
    return next(x for x in published[0]["facility-observations.json"]["locations"] if x["disclosure_location_id"] == key)


def check(row):
    checks = verifier.Checks()
    verifier.check_municipal_source_cells(row, checks)
    return checks


def test_all_real_published_rows_pass_independent_cells_and_full_disclosure_contract(published):
    objects, coverage = published
    assert coverage["disclosure_locations"] == 78
    assert coverage["disclosure_observations"] == 183
    checks = verifier.Checks()
    verifier.validate_disclosures(objects, coverage, checks)
    assert checks.failures == 0, checks.errors
    assert checks.count > 1000


@pytest.mark.parametrize("city", ["nyc", "seattle"])
@pytest.mark.parametrize("mutation", ["value", "unit", "year", "property", "field", "source_hash", "source_url", "meter", "scope", "omit", "boolean"])
def test_tampered_published_numbers_identity_or_boundary_fails(published, city, mutation):
    loc = location(published, city)
    row = next(x for x in loc["observations"] if x["metric"] == "electricity_consumption")
    if mutation == "value": row["value"] += 1
    elif mutation == "unit": row["unit"] = "MWh"
    elif mutation == "year":
        row["data_year"] += 1
        row["period_start"] = f"{row['data_year']}-01-01T00:00:00Z"
        row["period_end"] = f"{row['data_year']+1}-01-01T00:00:00Z"
    elif mutation == "property": loc["raw_source_row"]["property_id" if city == "nyc" else "osebuildingid"] = "999999"
    elif mutation == "field": row["source_field"] = "unrelated_energy_total"
    elif mutation == "source_hash": row["source_sha256"] = "0" * 64
    elif mutation == "source_url":
        loc["source_url"] = "https://example.com/unrelated.json"
        for obs in loc["observations"]: obs["source_url"] = loc["source_url"]
    elif mutation == "meter": row["is_meter_observation"] = True
    elif mutation == "scope": loc["raw_source_row"]["number_of_buildings" if city == "nyc" else "numberofbuildings"] = "2"
    elif mutation == "omit": loc["observations"].remove(row)
    else: row["value"] = True
    assert check(loc).failures > 0


@pytest.mark.parametrize("field", ["alert_energy_meter_has_less", "alert_energy_meter_has_gaps", "alert_energy_meter_has",
                                    "alert_energy_no_meters", "alert_energy_meter_has_single"])
def test_coherently_relabelled_native_energy_warning_still_cannot_publish_positive_values(published, field):
    loc = location(published)
    loc["raw_source_row"][field] = "Possible Issue"
    loc["source_quality"][field] = "Possible Issue"
    loc["source_quality"]["energy_publication_status"] = "withheld_incomplete_or_unknown"
    for row in loc["observations"]: row["source_quality"] = copy.deepcopy(loc["source_quality"])
    checks = check(loc)
    assert checks.failures > 0
    assert any("publish flagged/missing" in x for x in checks.errors)


@pytest.mark.parametrize("mutation", ["water_coverage", "water_months", "energy_estimate", "water_estimate", "missing_cell", "water_unit"])
def test_water_definition_estimates_and_unknown_values_are_not_reinterpreted(published, mutation):
    loc = location(published)
    water = next(x for x in loc["observations"] if x["metric"] == "water_use_reported")
    field = {"water_coverage": "metered_areas_water", "water_months": "alert_water_meter_has_less",
             "energy_estimate": "estimated_values_energy", "water_estimate": "estimated_values_water",
             "missing_cell": "water_use_all_water_sources"}.get(mutation)
    if mutation == "water_unit": water["unit"] = "million gallons"
    else:
        value = {"water_coverage": "Common Areas Only", "water_months": "Possible Issue",
                 "energy_estimate": "Yes", "water_estimate": "Yes", "missing_cell": None}[mutation]
        loc["raw_source_row"][field] = value
        if field in loc["source_quality"]: loc["source_quality"][field] = value
        for row in loc["observations"]: row["source_quality"] = copy.deepcopy(loc["source_quality"])
    assert check(loc).failures > 0


@pytest.mark.parametrize("field,value", [("compliancestatus", "Not Compliant"), ("complianceissue", "Missing Data"), ("demolished", True)])
def test_seattle_positive_values_require_all_native_qualification_fields(published, field, value):
    loc = location(published, "seattle")
    loc["raw_source_row"][field] = value
    loc["source_quality"][field] = value
    for row in loc["observations"]: row["source_quality"] = copy.deepcopy(loc["source_quality"])
    assert check(loc).failures > 0


def test_full_disclosure_entry_point_runs_cell_gate_even_with_valid_receipts(published):
    objects, coverage = published
    row = location(published)["observations"][0]
    row["value"] += 10
    checks = verifier.Checks()
    verifier.validate_disclosures(objects, coverage, checks)
    assert any("published quantity differs from native cell" in x for x in checks.errors)
