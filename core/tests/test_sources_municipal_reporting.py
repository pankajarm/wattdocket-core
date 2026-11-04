"""Real publisher records plus corruption/coverage and pagination adversaries."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

from wattdocket.production import sources_municipal_reporting as source

ROOT = Path(__file__).parent / "fixtures/municipal_reporting"
NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def captured(city):
    saved = json.loads((ROOT / f"{city}_benchmarking.json").read_text())
    return [(row, url, data["receipt"]["sha256"]) for url, data in saved.items() for row in data["payload"]]


def nyc_row():
    return next(x for x in captured("nyc") if x[0]["property_id"] == "2614608")


def test_live_nyc_capture_preserves_water_definition_mixed_scope_and_exact_native_values():
    data = source.parse_nyc_rows(captured("nyc"), 2024)
    assert len(data["locations"]) == 66
    assert len(data["observations"]) == 161
    assert len(data["withheld_metrics"]) == 37
    western = [o for o in data["observations"] if o["disclosure_location_id"] == "nyc-ll84:2614608"]
    values = {x["metric"]: (x["value"], x["unit"]) for x in western}
    assert values == {"electricity_consumption": (85542710, "kWh"), "water_use_reported": (18566.6, "kgal"),
                      "reported_ghg_total": (34246.82, "metric tons CO2e")}
    assert all(o["data_year"] == 2024 and o["period_end"] == "2025-01-01T00:00:00Z" for o in western)
    assert not any(o["metric"] in {"water_withdrawal", "water_consumption", "emissions_scope1"} for o in data["observations"])
    loc = next(x for x in data["locations"] if x["disclosure_location_id"] == "nyc-ll84:2614608")
    assert loc["source_identifiers"]["nyc_bin"] == "1001572"
    assert loc["source_identifiers"]["nyc_bbl"] == "1001440040"
    assert "Office" in loc["source_property_uses"] and "Retail Store" in loc["source_property_uses"]
    assert loc["data_center_floor_area_sqft"] == 620516
    assert "not isolated datacenter" in loc["scope_note"]
    assert all(x["facility_binding_verified"] is False and x["is_meter_observation"] is False for x in western)
    assert all("source_quote" not in x for x in western)


def test_actual_long_energy_meter_entry_warning_keeps_water_but_withholds_energy():
    row = next(x for x in captured("nyc") if x[0]["property_id"] == "6312193")
    assert row[0]["alert_energy_meter_has_single"] == "Possible Issue"
    data = source.parse_nyc_rows([row], 2024)
    assert [x["metric"] for x in data["observations"]] == ["water_use_reported"]
    assert len(data["withheld_metrics"]) == 2


def test_energy_incomplete_water_complete_is_not_a_whole_source_failure():
    row, url, sha = nyc_row()
    row["alert_energy_meter_has_gaps"] = "Possible Issue"
    data = source.parse_nyc_rows([(row, url, sha)], 2024)
    assert [x["metric"] for x in data["observations"]] == ["water_use_reported"]
    assert {x["metric"] for x in data["withheld_metrics"]} == {"electricity_consumption", "reported_ghg_total"}


def test_water_partial_meter_coverage_does_not_impersonate_whole_building_water():
    row, url, sha = nyc_row()
    row["metered_areas_water"] = "Common Areas Only"
    data = source.parse_nyc_rows([(row, url, sha)], 2024)
    assert {x["metric"] for x in data["observations"]} == {"electricity_consumption", "reported_ghg_total"}
    assert data["withheld_metrics"][0]["metric"] == "water_use_reported"


@pytest.mark.parametrize("field", [*source.NYC_ENERGY_FLAGS, "metered_areas_energy"])
def test_missing_completeness_flags_are_unknown_not_implicitly_ok(field):
    row, url, sha = nyc_row()
    row.pop(field)
    data = source.parse_nyc_rows([(row, url, sha)], 2024)
    assert {x["metric"] for x in data["observations"]} == {"water_use_reported"}


def test_estimates_remain_visible_without_becoming_sensor_data():
    row, url, sha = nyc_row()
    row["estimated_values_energy"] = "Yes"
    data = source.parse_nyc_rows([(row, url, sha)], 2024)
    electricity = next(x for x in data["observations"] if x["metric"] == "electricity_consumption")
    assert electricity["reporting_basis"] == "reported_annual_usage_includes_estimates"
    assert "estimated_values_energy: Yes" in electricity["methodology_note"]
    assert electricity["is_meter_observation"] is False


def test_missing_zero_and_campus_scope_are_distinct():
    row, url, sha = nyc_row()
    row["water_use_all_water_sources"] = "0"
    row["electricity_use_grid_purchase_2"] = "Not Available"
    row["number_of_buildings"] = "2"
    data = source.parse_nyc_rows([(row, url, sha)], 2024)
    assert data["locations"][0]["reporting_scope"] == "campus"
    assert not any(x["metric"] == "electricity_consumption" for x in data["observations"])
    assert next(x["value"] for x in data["observations"] if x["metric"] == "water_use_reported") == 0
    assert all(x["reporting_scope"] == "campus" for x in data["observations"])


@pytest.mark.parametrize("bad", [True, -2, "-2", "NaN", "Infinity", "unknown quantity"])
def test_malformed_quantity_fails_instead_of_silently_becoming_missing(bad):
    row, url, sha = nyc_row()
    row["water_use_all_water_sources"] = bad
    with pytest.raises(source.MunicipalReportingError, match="quantity"):
        source.parse_nyc_rows([(row, url, sha)], 2024)


@pytest.mark.parametrize("change", ["year", "date", "classification", "duplicate", "address"])
def test_nyc_identity_period_and_selection_corruption_rejects(change):
    row, url, sha = nyc_row()
    rows = [(row, url, sha)]
    if change == "year": row["report_year"] = "2025"
    elif change == "date": row["year_ending"] = "2024-06-30T00:00:00.000"
    elif change == "classification":
        for key in ("primary_property_type_self", "primary_property_type", "largest_property_use_type"): row[key] = "Office"
    elif change == "duplicate": rows.append(copy.deepcopy(rows[0]))
    else: row["address_1"] = ""
    with pytest.raises(source.MunicipalReportingError): source.parse_nyc_rows(rows, 2024)


def test_live_seattle_2025_available_even_when_dataset_page_description_lags():
    data = source.parse_seattle_rows(captured("seattle"), 2025)
    assert len(data["locations"]) == 12 and len(data["observations"]) == 22
    assert len(data["withheld_metrics"]) == 2
    flagged = next(x for x in data["locations"] if x["disclosure_location_id"] == "seattle-benchmarking:428")
    assert flagged["raw_source_row"]["electricity_kwh"] == "3222274"
    assert not any(x["disclosure_location_id"] == flagged["disclosure_location_id"] for x in data["observations"])
    assert all(x["data_year"] == 2025 and x["period_end"] == "2026-01-01T00:00:00Z" for x in data["observations"])
    assert all(not x["metric"].startswith("water_") for x in data["observations"])
    assert next(x["value"] for x in data["observations"] if x["disclosure_location_id"] == "seattle-benchmarking:131" and x["metric"] == "electricity_consumption") == 1525057


class PagedPublisher:
    def __init__(self, mutation=None):
        self.rows = [x[0] for x in captured("nyc")[:3]]
        self.mutation, self.meta_calls, self.calls = mutation, 0, []

    def get_bytes(self, url):
        self.calls.append(url)
        if "/api/views/" in url:
            self.meta_calls += 1
            labels = {v[0]: v[2] for v in source.NYC_METRICS.values()}
            if self.mutation == "unit": labels["water_use_all_water_sources"] = "Water use (liters)"
            data = {"id": source.NYC_ID, "rowsUpdatedAt": 100 + (self.meta_calls if self.mutation == "revision" else 0),
                    "name": "Fixture municipal publication", "columns": [{"fieldName": f, "name": labels.get(f, f)} for f in source.NYC_FIELDS]}
        else:
            q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
            if q["$select"] == "report_year": data = [{"report_year": "2026" if self.mutation == "unfinished_year" else "2024"}]
            elif q["$select"] == "count(*) AS n": data = [{"n": "1001" if self.mutation == "count" else "3"}]
            else:
                offset, limit = int(q["$offset"]), int(q["$limit"])
                data = self.rows[offset:offset+limit]
                if self.mutation == "repeated" and offset: data = self.rows[:1]
                if self.mutation == "short" and not offset: data = data[:1]
        return json.dumps(data).encode()


def page_capture(fetcher):
    return source._latest_capture(fetcher, base=source.NYC_BASE, dataset=source.NYC_ID,
        year_field="report_year", id_field="property_id", selection=source.NYC_FILTER,
        fields=source.NYC_FIELDS, metrics=source.NYC_METRICS, now=NOW, page_size=2)


def test_pagination_archives_distinct_page_urls_and_completes_all_declared_rows():
    rows, year, evidence = page_capture(PagedPublisher())
    assert len(rows) == 3 and year == 2024
    assert len({x[1] for x in rows}) == 2
    assert evidence["selected_property_count"] == 3
    assert [x["rows"] for x in evidence["pages"]] == [2, 1]


@pytest.mark.parametrize("mutation", ["unit", "revision", "unfinished_year", "count", "repeated", "short"])
def test_pagination_schema_revision_and_complete_year_gates_fail_closed(mutation):
    with pytest.raises(source.MunicipalReportingError): page_capture(PagedPublisher(mutation))
