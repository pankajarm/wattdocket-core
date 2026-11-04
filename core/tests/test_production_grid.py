"""Production grid contracts: missing data and forecasts cannot become facts."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wattdocket.production.sources_grid import (
    EGRID_DISCOVERY_URL, EIA_BASE_URL, EIA_MANIFEST_URL, EIA_RESPONDENTS_URL,
    ERCOT_URL, GridSourceError, collect_egrid, collect_eia930, collect_ercot,
    discover_egrid_workbook, discover_eia_balance_files, parse_eia_balance, parse_ercot,
    _validate_geojson, simplify_geojson, collect_egrid_geometry, geometry_urls, _repair_polygon_parts,
)

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 15, 20, tzinfo=UTC)
EGRID_FIXTURE = Path(__file__).parent / "fixtures" / "egrid_srl23.xlsx"


class Fetcher:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def get_bytes(self, url):
        self.calls.append(url)
        return self.values[url]

    def get_json(self, url):
        self.calls.append(url)
        return self.values[url]


def csv_bytes(rows, columns=None):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns or list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def balance_row(**changes):
    row = {"Balancing Authority": "ERCO", "UTC Time at End of Hour": "11/03/2026 2:00:00 PM",
           "Demand (MW)": "100", "Demand Forecast (MW)": "120", "Net Generation (MW)": "110",
           "Total Interchange (MW)": "10", "Region": "TEX",
           "Net Generation (MW) from Coal": "", "Net Generation (MW) from Natural Gas": "0",
           "Net Generation (MW) from Battery Storage": "-20",
           "Net Generation (MW) from Coal (Imputed)": "42", "Demand (MW) (Adjusted)": "999"}
    row.update(changes)
    return row


def ercot_fuels(**changes):
    values = {"Coal and Lignite": 10, "Natural Gas": 20, "Nuclear": 30,
              "Wind": 10, "Solar": 0, "Hydro": 0, "Other": 0,
              "Power Storage": -12, "Power Storage Charging": -15, "Power Storage Discharging": 3}
    values.update(changes)
    return {key: {"gen": value} for key, value in values.items()}


def test_official_egrid_discovery_rejects_third_party_and_picks_newest_revision():
    page = b'''<a href="https://other.example/egrid2025_data.xlsx">unofficial</a>
      <a href="/system/files/documents/egrid2023_data.xlsx">old</a>
      <a href="/system/files/documents/egrid2023_data_rev2.xlsx">current</a>'''
    url, year, revision = discover_egrid_workbook(page)
    assert (year, revision) == (2023, 2)
    assert url == "https://www.epa.gov/system/files/documents/egrid2023_data_rev2.xlsx"
    with pytest.raises(GridSourceError):
        discover_egrid_workbook(b'<a href="https://example.com/egrid2024_data.xlsx">new</a>')


def test_collect_egrid_keeps_data_year_separate_from_retrieval():
    url = "https://www.epa.gov/system/files/documents/2025-06/egrid2023_data_rev2.xlsx"
    source = collect_egrid(Fetcher({EGRID_DISCOVERY_URL: f'<a href="{url}">Data</a>'.encode(),
                                   url: EGRID_FIXTURE.read_bytes()}), now=NOW)
    assert source.record_count == 27
    assert source.observation_end == "2024-01-01T00:00:00Z"
    assert source.data["source"]["checked_at"] == "2026-11-03T15:20:00Z"
    assert source.data["rates"]["ERCT"]["co2e_rate_lb_per_mwh"] == pytest.approx(736.629, abs=0.01)
    assert source.data["rates"]["ERCT"]["source_url"] == url


def test_eia_manifest_fetches_both_halves_across_year_boundary():
    data = csv_bytes([
        {"FILENAME": "EIA930_BALANCE_2026_Jul_Dec.csv", "YEAR": "2026", "DESCRIPTION": "BALANCE"},
        {"FILENAME": "EIA930_BALANCE_2027_Jan_Jun.csv", "YEAR": "2027", "DESCRIPTION": "BALANCE"},
        {"FILENAME": "../../private.csv", "YEAR": "2027", "DESCRIPTION": "BALANCE"},
    ])
    found = discover_eia_balance_files(data, now=datetime(2027, 1, 2, tzinfo=UTC))
    assert [row["filename"] for row in found] == ["EIA930_BALANCE_2026_Jul_Dec.csv", "EIA930_BALANCE_2027_Jan_Jun.csv"]


def test_eia_utc_hour_ending_null_and_negative_storage_survive():
    regions, _ = parse_eia_balance(csv_bytes([balance_row()]), now=NOW)
    row = regions["ERCO"]["by_time"]["2026-11-03T13:00:00Z"]
    assert row["hour_convention"] == "END"
    assert row["demand_mw"] == 100
    assert row["fuel_mix"] == {"natural_gas": 0, "battery_storage": -20}
    assert row["missing_fuels"] == ["coal"]
    assert "42" not in str(row)  # Imputation never silently fills the reported stream.


def test_eia_future_forecast_and_forecast_only_rows_never_count_as_observed():
    rows = [balance_row(), balance_row(**{"UTC Time at End of Hour": "11/04/2026 1:00:00 PM"}),
            balance_row(**{"UTC Time at End of Hour": "11/03/2026 3:00:00 PM", "Demand (MW)": "",
                           "Net Generation (MW)": "", "Total Interchange (MW)": "",
                           "Net Generation (MW) from Natural Gas": "", "Net Generation (MW) from Battery Storage": ""})]
    regions, _ = parse_eia_balance(csv_bytes(rows), now=NOW)
    assert len(regions["ERCO"]["by_time"]) == 2
    latest = regions["ERCO"]["by_time"]["2026-11-03T14:00:00Z"]
    assert latest["quality"] == "missing"
    assert latest["forecast_demand_mw"] == 120


@pytest.mark.parametrize("number", ["NaN", "inf", "not-a-number"])
def test_eia_rejects_bad_observed_numbers(number):
    with pytest.raises(GridSourceError):
        parse_eia_balance(csv_bytes([balance_row(**{"Demand (MW)": number})]), now=NOW)


def test_eia_conflicting_duplicate_is_not_last_row_wins():
    with pytest.raises(GridSourceError, match="conflicting duplicate"):
        parse_eia_balance(csv_bytes([balance_row(), balance_row(**{"Demand (MW)": "101"})]), now=NOW)
    regions, _ = parse_eia_balance(csv_bytes([balance_row(), balance_row()]), now=NOW)
    assert len(regions["ERCO"]["by_time"]) == 1


def test_eia_html_200_and_schema_change_refuse():
    with pytest.raises(GridSourceError, match="HTML"):
        parse_eia_balance(b"<!doctype html><p>not found</p>", now=NOW)
    with pytest.raises(GridSourceError, match="missing columns"):
        parse_eia_balance(b"Name,Value\na,1\n", now=NOW)
    complete = csv_bytes([balance_row()]).decode().splitlines()
    # Required identity columns can survive even when trailing fuel fields are cut off.
    truncated = (complete[0] + "\n" + complete[1].rsplit(",", 1)[0] + "\n").encode()
    with pytest.raises(GridSourceError, match="truncated"):
        parse_eia_balance(truncated, now=NOW)


def test_eia_collection_fills_gaps_and_uses_observation_clock():
    filename = "EIA930_BALANCE_2026_Jul_Dec.csv"
    manifest = csv_bytes([{"FILENAME": filename, "YEAR": "2026", "DESCRIPTION": "BALANCE", "LAST_UPDATED": "11/03/2026 15:00"}])
    rows = [balance_row(**{"Balancing Authority": f"B{i:02}"}) for i in range(50)]
    rows.append(balance_row(**{"Balancing Authority": "B00", "UTC Time at End of Hour": "11/04/2026 1:00:00 PM"}))
    fetcher = Fetcher({EIA_MANIFEST_URL: manifest, EIA_BASE_URL + filename: csv_bytes(rows),
                       EIA_RESPONDENTS_URL: [{"data": [{"id": "B00", "name": "Authority zero"}]}]})
    result = collect_eia930(fetcher, now=NOW)
    assert result.record_count == 50
    assert result.data["authorities_with_observations"] == 50
    assert result.observation_end == "2026-11-03T14:00:00Z"
    region = result.data["regions"][0]
    assert region["name"] == "Authority zero"
    assert region["readings_count"] == 168
    assert region["missing_readings_count"] == 167
    assert region["latest_demand_mw"] == 100
    assert region["readings"][-1]["demand_mw"] is None
    assert region["intensity_available"] is False


@pytest.mark.parametrize("forecast_only_extra", [False, True])
def test_eia_national_gate_counts_distinct_observed_authorities(forecast_only_extra):
    filename = "EIA930_BALANCE_2026_Jul_Dec.csv"
    manifest = csv_bytes([{"FILENAME": filename, "YEAR": "2026", "DESCRIPTION": "BALANCE"}])
    # 98 observations pass a row-count gate but do not establish national coverage.
    rows = [balance_row(**{"Balancing Authority": f"B{i:02}", "UTC Time at End of Hour": f"11/03/2026 {hour}:00:00 PM"})
            for i in range(49) for hour in (1, 2)]
    if forecast_only_extra:
        rows.append(balance_row(**{"Balancing Authority": "B49", "Demand (MW)": "", "Net Generation (MW)": "",
                                   "Total Interchange (MW)": "", "Net Generation (MW) from Natural Gas": "",
                                   "Net Generation (MW) from Battery Storage": ""}))
    fetcher = Fetcher({EIA_MANIFEST_URL: manifest, EIA_BASE_URL + filename: csv_bytes(rows),
                       EIA_RESPONDENTS_URL: [{"data": [{"id": "B00", "name": "Authority zero"}]}]})
    with pytest.raises(GridSourceError, match="coverage unexpectedly small"):
        collect_eia930(fetcher, now=NOW)


def test_ercot_missing_interval_and_null_fuel_remain_missing():
    payload = {"lastUpdated": "2026-11-03 00:11:00-0600", "data": {"2026-11-03": {
        "2026-11-03 00:00:00-0600": ercot_fuels(),
        "2026-11-03 00:10:00-0600": ercot_fuels(**{"Natural Gas": None}),
    }}}
    result = collect_ercot(Fetcher({ERCOT_URL: payload}), now=NOW)
    region = result.data["regions"][0]
    assert result.record_count == 2
    first, missing, partial = region["readings"]
    assert first["generation_mw"] == 70  # MW, not MWh; storage is not added twice.
    assert first["storage_mw"] == -12
    assert missing["quality"] == "missing" and missing["generation_mw"] is None
    assert partial["quality"] == "partial" and partial["generation_mw"] is None
    assert "natural_gas" not in partial["fuel_mix"]
    assert region["missing_readings_count"] == 1
    assert all(row["intensity_lb_co2e_per_mwh"] is None for row in region["readings"])


def test_ercot_dst_fallback_keeps_both_local_hours_and_300_expected_intervals():
    payload = {"lastUpdated": "2026-11-02 00:01:00-0600", "data": {"2026-11-01": {
        "2026-11-01 01:00:00-0500": ercot_fuels(),
        "2026-11-01 01:00:00-0600": ercot_fuels(),
    }}}
    region = parse_ercot(payload, now=NOW)
    assert region["days"][0]["expected_intervals"] == 300
    assert region["days"][0]["reported_intervals"] == 2
    reported = [row["interval_start_utc"] for row in region["readings"] if row["quality"] == "reported"]
    assert reported == ["2026-11-01T06:00:00Z", "2026-11-01T07:00:00Z"]


def test_ercot_unknown_fuel_does_not_disappear():
    fuels = ercot_fuels()
    fuels["New Fuel"] = {"gen": 50}
    payload = {"lastUpdated": "2026-11-03 00:01:00-0600", "data": {"2026-11-03": {"2026-11-03 00:00:00-0600": fuels}}}
    with pytest.raises(GridSourceError, match="unrecognized fuel"):
        parse_ercot(payload, now=NOW)


def test_ercot_future_source_clock_refuses():
    payload = {"lastUpdated": "2027-01-01 00:01:00-0600", "data": {"2027-01-01": {"2027-01-01 00:00:00-0600": ercot_fuels()}}}
    with pytest.raises(GridSourceError, match="future"):
        parse_ercot(payload, now=NOW)


def test_geometry_holes_and_islands_survive_normalization():
    from shapely.geometry import Point, shape
    exterior = [[0, 0], [3, 0], [3, 3], [0, 3], [0, 0]]
    hole = [[1, 1], [1, 2], [2, 2], [2, 1], [1, 1]]
    island = [[5, 0], [5.0001, 0], [5.0001, .0001], [5, .0001], [5, 0]]
    payload = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"Subregion": "ERCT"},
                "geometry": {"type": "MultiPolygon", "coordinates": [[exterior, hole], [island]]}}]}
    checked = _validate_geojson(payload, expected_count=1, overlap=False)
    result = simplify_geojson(checked)
    feature = result["features"][0]
    geometry = shape(feature["geometry"])
    assert feature["properties"]["subregion"] == "ERCT"
    assert feature["properties"]["geometry_valid"] is True
    assert len(geometry.geoms) == 2
    assert sum(len(part.interiors) for part in geometry.geoms) == 1
    assert not geometry.contains(Point(1.5, 1.5))
    assert geometry.contains(Point(.5, .5))


def test_geometry_refuses_transfer_truncation_and_invalid_coordinate():
    with pytest.raises(GridSourceError, match="truncated"):
        _validate_geojson({"type": "FeatureCollection", "features": [], "exceededTransferLimit": True}, expected_count=1, overlap=False)
    payload = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"MultipleSu": "Yes"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [300, 0], [0, 1], [0, 0]]]}}]}
    with pytest.raises(GridSourceError, match="bounds"):
        _validate_geojson(payload, expected_count=1, overlap=True)


def test_geometry_collection_requires_both_complete_layers():
    workbook = "https://www.epa.gov/egrid2023_data_rev2.xlsx"
    values = {EGRID_DISCOVERY_URL: f'<a href="{workbook}">Current</a>'.encode()}
    for overlap in (False, True):
        urls = geometry_urls(2023, overlap=overlap)
        values[urls["metadata"]] = {"fields": [{"name": "MultipleSu" if overlap else "Subregion"}],
                                   "editingInfo": {"dataLastEditDate": 1737082370943}}
        count = 1 if overlap else 27
        values[urls["count"]] = {"count": count}
        features = [{"type": "Feature", "properties": {"MultipleSu": "Yes"} if overlap else {"Subregion": f"G{i:02}"},
                     "geometry": {"type": "Polygon", "coordinates": [[[i, 0], [i + .5, 0], [i + .5, .5], [i, .5], [i, 0]]]}}
                    for i in range(count)]
        values[urls["geojson"]] = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    result = collect_egrid_geometry(Fetcher(values), now=NOW)
    assert result.record_count == 27
    assert len(result.data["overlap_geojson"]["features"]) == 1
    assert result.data["tolerance_degrees"] == .001
    assert result.data["boundary_buffer_degrees"] > .002
    assert result.data["normalized_sha256"] != result.data["sha256"]
    values[geometry_urls(2023, overlap=True)["count"]] = {"count": 2}
    with pytest.raises(GridSourceError, match="count disagrees"):
        collect_egrid_geometry(Fetcher(values), now=NOW)


def test_geometry_overlapping_parts_use_union_not_even_odd_exclusion():
    from shapely.geometry import MultiPolygon, Point, box
    original = MultiPolygon([box(0, 0, 2, 2), box(1, 0, 3, 2)])
    assert original.is_valid is False
    repaired, receipt = _repair_polygon_parts(original)
    assert repaired.is_valid and repaired.contains(Point(1.5, 1))
    assert repaired.area == 6
    assert receipt["source_additive_area_degrees2"] == 8
    assert receipt["duplicate_overlap_area_degrees2"] == 2
    assert receipt["maximum_part_relative_area_change"] == 0


def test_geometry_repair_refuses_area_invention_from_crossing_ring():
    from shapely.geometry import Polygon
    bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    with pytest.raises(GridSourceError, match="changes area beyond tolerance"):
        _repair_polygon_parts(bowtie)


def test_geometry_linework_repairs_self_touching_ring_without_area_change():
    from shapely.geometry import Polygon
    # A zero-area spike touches its own ring. Repair removes the collapsed
    # line, while preserving the two-dimensional area exactly.
    polygon = Polygon([(0, 0), (2, 0), (2, 2), (1, 2), (1, 3), (1, 2), (0, 2), (0, 0)])
    repaired, receipt = _repair_polygon_parts(polygon)
    assert repaired.is_valid
    assert repaired.area == polygon.area == 4
    assert receipt["repaired"] is True
    assert receipt["degenerate_linework_parts"] == 1


def test_geometry_retains_valid_original_if_geos_simplification_is_invalid(monkeypatch):
    import shapely
    from shapely.geometry import Polygon, box, mapping, shape
    original = box(0, 0, 2, 2)
    invalid = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    monkeypatch.setattr(shapely, "simplify", lambda *args, **kwargs: invalid)
    payload = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"Subregion": "ERCT"}, "geometry": mapping(original)}]}
    feature = simplify_geojson(payload)["features"][0]
    assert shape(feature["geometry"]).equals(original)
    receipt = feature["properties"]["geometry_transform"]
    assert receipt["local_simplification_tolerance_degrees"] == 0
    assert len(receipt["rejected_simplification_attempts"]) == 3
    assert feature["properties"]["geometry_valid"] is True
