"""Adversarial source-contract checks using captured primary publisher rows."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from wattdocket.production.sources_facility_reporting import (
    FacilityReportingError, MA_APP, MA_LAYER, MA_ELECTRICITY, MA_GHG,
    GHGRP_DIM_URL, collect_massachusetts_reporting, parse_massachusetts,
    parse_ghgrp, ma_location_id,
)

FIXTURES = Path(__file__).parent / "fixtures" / "facility_reporting"
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def ma_capture():
    source = json.loads((FIXTURES / "massachusetts_energy.json").read_text())
    url, capture = next(iter(source.items()))
    return [x["attributes"] for x in capture["payload"]["features"]], url, capture["receipt"]["sha256"]


def ma_parse(rows=None):
    original, url, sha = ma_capture()
    return parse_massachusetts(original if rows is None else rows, source_url=url,
                               source_sha256=sha, now=NOW)


def ghg_capture():
    source = json.loads((FIXTURES / "ghgrp.json").read_text())
    dimension = source[GHGRP_DIM_URL]
    emissions = {}
    for url, capture in source.items():
        if "PUB_FACTS" in url:
            fid = url.split("/FACILITY_ID/")[1].split("/")[0]
            emissions[fid] = (capture["payload"], url, capture["receipt"]["sha256"])
    return dimension["payload"], emissions, dimension["receipt"]["sha256"]


def ghg_parse(dimension=None, emissions=None):
    raw_dimension, raw_emissions, sha = ghg_capture()
    return parse_ghgrp(raw_dimension if dimension is None else dimension,
                       raw_emissions if emissions is None else emissions,
                       dimension_sha256=sha, now=NOW)


def test_real_massachusetts_energy_retains_native_unit_and_missing_is_not_zero():
    data = ma_parse()
    assert len(data["locations"]) == 9
    assert len(data["observations"]) == 14
    equinix = ma_location_id("87JCHQ2M+7VV-32-27-31-23")
    row = next(x for x in data["observations"] if x["disclosure_location_id"] == equinix
               and x["metric"] == "electricity_consumption")
    assert (row["value"], row["unit"], row["data_year"]) == (23932780, "kBtu", 2025)
    assert row["period_end"] == "2026-01-01T00:00:00Z"
    assert row["facility_binding_verified"] is False
    missing = ma_location_id("87J9692R+XVR-18-18-17-18")
    assert not any(x["disclosure_location_id"] == missing for x in data["observations"])
    location = next(x for x in data["locations"] if x["disclosure_location_id"] == missing)
    assert "electricity_consumption" in location["missing_metrics"]
    assert not any(x["metric"].startswith("water_") for x in data["observations"])
    assert {x["reporting_basis"] for x in data["observations"] if x["metric"] == "reported_ghg_total"} == {
        "publisher_calculated_from_reported_energy"}


def test_building_address_scope_is_not_replaced_by_inventory_city_or_campus():
    data = ma_parse()
    centennial = next(x for x in data["locations"] if x["address"]["street"] == "8 Centennial Dr")
    assert centennial["address"]["city"] == "Shrewsbury"  # ECHO candidate says Grafton.
    second = next(x for x in data["locations"] if x["address"]["street"] == "115 2Nd Ave")
    assert second["reporting_scope"] == "building"  # FRS covers 115 AND 125.
    assert second["operator"] is None


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf"), "123"])
def test_invalid_quantities_reject_instead_of_becoming_measurements(bad):
    rows, _, _ = ma_capture()
    rows[0][MA_ELECTRICITY] = bad
    with pytest.raises(FacilityReportingError, match="quantity"):
        ma_parse(rows)


def test_observed_zero_remains_zero_and_never_fills_other_missing_cells():
    rows, _, _ = ma_capture()
    next(x for x in rows if x[MA_ELECTRICITY] is not None)[MA_ELECTRICITY] = 0
    data = ma_parse(rows)
    assert any(x["metric"] == "electricity_consumption" and x["value"] == 0 for x in data["observations"])
    assert len(data["observations"]) == 14


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "campus", "field_removed"])
def test_massachusetts_partial_identity_or_scope_change_cannot_publish(mutation):
    rows, _, _ = ma_capture()
    if mutation == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif mutation == "missing":
        rows.pop()
    elif mutation == "campus":
        rows[0]["Parent_Child_Status"] = "Parent"
    else:
        rows[0].pop(MA_ELECTRICITY)
    with pytest.raises(FacilityReportingError):
        ma_parse(rows)


def test_massachusetts_discovery_new_edition_rejects_old_columns():
    class Fetcher:
        def get_bytes(self, url):
            assert url == MA_APP
            return json.dumps({"sources": [{"url": MA_LAYER}, {"url": MA_LAYER.replace("2026_10_28", "2027_10_28")}]}).encode()
    with pytest.raises(FacilityReportingError, match="latest annual edition"):
        collect_massachusetts_reporting(Fetcher(), now=NOW)


def test_massachusetts_changed_unit_rejects_without_downloading_quantities():
    class Fetcher:
        def get_bytes(self, url):
            if url == MA_APP:
                return json.dumps({"url": MA_LAYER}).encode()
            assert url == MA_LAYER + "?f=pjson"
            return json.dumps({"fields": [{"name": MA_ELECTRICITY, "alias": "Electricity demand kW"},
                                           {"name": MA_GHG, "alias": "2025 Total GHG Emissions Metric Tons COe"}]}).encode()
    with pytest.raises(FacilityReportingError, match="units or year"):
        collect_massachusetts_reporting(Fetcher(), now=NOW)


def test_real_ghgrp_reciprocal_ids_and_gas_total_do_not_allocate_contact_address():
    data = ghg_parse()
    assert len(data["locations"]) == 6 and len(data["observations"]) == 10
    nd = next(x for x in data["locations"] if x["disclosure_location_id"] == "ghgrp:1014631")
    assert nd["source_identifiers"]["frs"] == "110071397412"
    row = next(x for x in data["observations"] if x["disclosure_location_id"] == "ghgrp:1014631" and x["data_year"] == 2023)
    assert row["value"] == 41464.144  # 41421.4 + 19.5 + 23.244; already CO2e.
    assert row["unit"] == "metric tons CO2e"
    assert all(x["metric"] == "reported_ghg_total" and not x["facility_binding_verified"] for x in data["observations"])
    shared = [x for x in data["locations"] if x["source_identifiers"]["frs"] == "110071232148"]
    assert len(shared) == 4
    assert len({x["disclosure_location_id"] for x in shared}) == 4
    assert all(len(x["shared_frs_reporting_entities"]) == 4 for x in shared)


@pytest.mark.parametrize("mutation", ["other_facility", "other_subpart", "missing_gas", "duplicate_gas", "unmatched_year", "missing_value"])
def test_ghgrp_never_sums_wrong_scope_or_incomplete_components(mutation):
    _, emissions, _ = ghg_capture()
    rows = emissions["1014631"][0]
    if mutation == "other_facility":
        rows[0]["facility_id"] = 1014632
    elif mutation == "other_subpart":
        rows[0]["sub_part_id"] = 2
    elif mutation == "missing_gas":
        rows.pop()
    elif mutation == "duplicate_gas":
        rows.append(deepcopy(rows[0]))
    elif mutation == "unmatched_year":
        rows[0]["year"] = 2024
    else:
        rows[0]["co2e_emission"] = None
    with pytest.raises(FacilityReportingError):
        ghg_parse(emissions=emissions)


def test_ghgrp_biogenic_gas_is_not_added_to_nonbiogenic_total():
    _, emissions, _ = ghg_capture()
    rows = emissions["1014631"][0]
    rows.append({"facility_id": 1014631, "sub_part_id": 1, "gas_id": 8, "year": 2023, "co2e_emission": 999999})
    data = ghg_parse(emissions=emissions)
    assert next(x["value"] for x in data["observations"] if x["disclosure_location_id"] == "ghgrp:1014631" and x["data_year"] == 2023) == 41464.144


def test_ghgrp_changed_crosswalk_or_industry_rejects_false_relationship():
    dim, _, _ = ghg_capture()
    dim[0]["frs_id"] = "999999999999"
    with pytest.raises(FacilityReportingError, match="relationship changed"):
        ghg_parse(dimension=dim)
    dim, _, _ = ghg_capture()
    dim[0]["naics_code"] = "221112"  # A power plant is not a datacenter observation.
    with pytest.raises(FacilityReportingError, match="classification"):
        ghg_parse(dimension=dim)
