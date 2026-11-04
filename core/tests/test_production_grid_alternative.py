"""Independent comparisons must preserve method, units, provenance and scope."""
import hashlib
import io
from datetime import datetime, timezone

import openpyxl
import pytest

from wattdocket.production.sources_grid import EGRID_CODES, GridSourceError, egrid_release_date
from wattdocket.production.sources_grid_alternative import (
    CODE_COMMIT, COMPARISON_NAME, METADATA_NAME, RECORD_API, RELEASE_VERSION, WORKBOOK_NAME,
    collect_cornerstone_egrid2024, file_url, normalize_cornerstone_2024,
)

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)
FIELDS = ["YEAR", "SUBRGN", "SRNAME", "SRNGENAN", "SRCO2AN", "SRCH4AN", "SRN2OAN", "SRCO2EQA",
          "SRCO2RTA", "SRCH4RTA", "SRN2ORTA"]
LABELS = ["Data Year", "eGRID subregion acronym", "eGRID subregion name", "annual generation (MWh)",
          "CO2 emissions (metric tons)", "CH4 emissions (kg)", "N2O emissions (kg)", "CO2e emissions (metric tons)",
          "CO2 rate (kg/MWh)", "CH4 rate (kg/MWh)", "N2O rate (kg/MWh)"]
PREFIX = "2024 development branch eGRID subregion "
COMPARE_HEADERS = ["eGRID subregion acronym", PREFIX + "annual CO2 total output emission rate (kg/MWh)",
                   PREFIX + "annual CH4 total output emission rate (kg/MWh)", PREFIX + "annual N2O total output emission rate (kg/MWh)",
                   PREFIX + "CO2e output emission rate (kg/MWh)"]


def workbook_bytes(rows, *, labels=None):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "SRL24"
    sheet.append(labels or LABELS)
    sheet.append(FIELDS)
    for row in rows:
        sheet.append([row[field] for field in FIELDS])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def comparison_bytes(rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(COMPARE_HEADERS)
    for row in rows:
        co2, ch4, n2o = (row[key] for key in ("SRCO2RTA", "SRCH4RTA", "SRN2ORTA"))
        sheet.append([row["SUBRGN"], co2, ch4, n2o, co2 + ch4 * 27 + n2o * 273])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def component_rows():
    return [{"YEAR": 2024, "SUBRGN": code, "SRNAME": "Region " + code, "SRNGENAN": 1000,
             "SRCO2AN": 100, "SRCH4AN": 100, "SRN2OAN": 10, "SRCO2EQA": 105.71,
             "SRCO2RTA": 100, "SRCH4RTA": .1, "SRN2ORTA": .01} for code in sorted(EGRID_CODES)]


class Fetcher:
    def __init__(self, values):
        self.values = values

    def get_bytes(self, url):
        return self.values[url]

    def get_json(self, url):
        return self.values[url]


def source_values():
    rows = component_rows()
    files = {WORKBOOK_NAME: workbook_bytes(rows), COMPARISON_NAME: comparison_bytes(rows),
             METADATA_NAME: ("Repository: https://github.com/USEPA/egrid\nBranch: development\nCommit: " + CODE_COMMIT +
                "\nEIA data retrieved: 2026-03-11T12:37:00-00:00\nCEMS data retrieved: 2026-03-11T12:31:00-00:00\n"
                "Script executed: eGRID_master.qmd\n").encode()}
    record = {"id": 18968658, "metadata": {"version": RELEASE_VERSION, "doi": "10.5281/zenodo.18968658",
              "publication_date": "2026-03-17", "license": {"id": "cc-by-4.0"}, "access_right": "open",
              "creators": [{"name": "Cornerstone Sustainability Data Initiative"}], "description": "AR5 CH4=28 N2O=265"},
              "files": [{"key": name, "size": len(raw), "checksum": "md5:" + hashlib.md5(raw).hexdigest(),
                         "links": {"self": file_url(name)}} for name, raw in files.items()]}
    return {RECORD_API: record, **{file_url(name): raw for name, raw in files.items()}}


def test_metric_mass_conversion_and_gwp_conflict_are_explicit_not_silently_replaced():
    rows = component_rows()
    rates, validation = normalize_cornerstone_2024(workbook_bytes(rows), comparison_bytes(rows))
    row = rates["NWPP"]
    assert row["co2e_rate_kg_per_mwh"] == pytest.approx(105.45)
    assert row["co2e_rate_lb_per_mwh"] == pytest.approx(105.45 / .45359237)
    assert row["published_ar6_rate_kg_per_mwh"] == pytest.approx(105.71)
    assert row["gwp_basis"] == "IPCC AR5" and row["published_annual_mass_gwp_basis"] == "IPCC AR6"
    assert row["geometry_binding_verified"] is row["can_replace_official_baseline"] is False
    assert row["source_role"] == "independent regional comparison"
    assert validation["status"] == "validated_with_disclosed_method_difference"
    assert validation["validated_regions"] == 27
    assert validation["comparison_gwp"] == "IPCC AR6 non-fossil methane"
    assert row["published_comparison_rate_kg_per_mwh"] == pytest.approx(105.43)
    assert row["published_comparison_gwp_factors"] == {"co2": 1, "ch4": 27, "n2o": 273}


@pytest.mark.parametrize("problem", ["short_ton_units", "missing_region", "duplicate_region", "wrong_year", "wrong_gwp", "bad_precision", "nan"])
def test_unresolved_units_coverage_or_method_refuse_all_comparisons(problem):
    rows, labels = component_rows(), list(LABELS)
    if problem == "short_ton_units":
        labels[4] = "CO2 emissions (tons)"
    elif problem == "missing_region":
        rows.pop()
    elif problem == "duplicate_region":
        rows[-1]["SUBRGN"] = rows[0]["SUBRGN"]
    elif problem == "wrong_year":
        rows[0]["YEAR"] = 2023
    elif problem == "wrong_gwp":
        rows[0]["SRCO2EQA"] = 115
    elif problem == "bad_precision":
        rows[0]["SRCO2RTA"] += 1
    else:
        rows[0]["SRNGENAN"] = "NaN"
    with pytest.raises(GridSourceError):
        normalize_cornerstone_2024(workbook_bytes(rows, labels=labels), comparison_bytes(rows))


def test_source_preserves_version_dates_license_hashes_and_unknown_input_offset():
    result = collect_cornerstone_egrid2024(Fetcher(source_values()), now=NOW)
    assert result.record_count == 27 and result.source_id == "cornerstone_egrid2024"
    assert result.published_at == "2026-03-17"
    assert result.observation_end == "2025-01-01T00:00:00Z"
    source = result.data["source"]
    assert source["checked_at"] == "2026-11-03T00:00:00Z"
    assert source["license"] == "CC-BY-4.0" and source["code_license"] == "MIT"
    assert source["input_retrieval_metadata"]["EIA"]["offset_known"] is False
    assert source["code_commit"] == CODE_COMMIT
    assert len(source["file_sha256"]) == 3
    assert result.data["rates"]["NWPP"]["workbook_sha256"] == source["workbook_sha256"]
    assert result.data["rates"]["NWPP"]["gwp_horizon_years"] == 100


def test_companion_comparison_method_change_is_not_confused_with_selected_ar5_calculation():
    rows = component_rows()
    workbook = openpyxl.load_workbook(io.BytesIO(comparison_bytes(rows)))
    # If the publisher changes the comparison to AR5 without a reviewed version,
    # its annual-mass AR6 validation alone cannot authorize the altered evidence.
    workbook.active.cell(2, 5, 105.45)
    output = io.BytesIO()
    workbook.save(output)
    with pytest.raises(GridSourceError, match="comparison no longer reconciles"):
        normalize_cornerstone_2024(workbook_bytes(rows), output.getvalue())


@pytest.mark.parametrize("problem", ["checksum", "version", "license", "file_host", "code_commit"])
def test_source_manifest_and_code_changes_require_review(problem):
    values = source_values()
    if problem == "checksum":
        values[file_url(WORKBOOK_NAME)] += b"changed"
    elif problem == "version":
        values[RECORD_API]["metadata"]["version"] = "2.0.0"
    elif problem == "license":
        values[RECORD_API]["metadata"]["license"]["id"] = "unknown"
    elif problem == "file_host":
        values[RECORD_API]["files"][0]["links"]["self"] = "https://unrelated.example/file"
    else:
        text = values[file_url(METADATA_NAME)].replace(CODE_COMMIT.encode(), b"a" * 40)
        values[file_url(METADATA_NAME)] = text
        entry = next(row for row in values[RECORD_API]["files"] if row["key"] == METADATA_NAME)
        entry["checksum"] = "md5:" + hashlib.md5(text).hexdigest()
    with pytest.raises(GridSourceError):
        collect_cornerstone_egrid2024(Fetcher(values), now=NOW)


def test_official_publication_date_is_tied_to_selected_edition_and_revision():
    page = b'<h2>eGRID with 2023 Data</h2><p>Released: 1/15/2025<br>Revision 1 Released: 1/17/2025<br>Revision 2 Released: 6/12/2025</p>'
    assert egrid_release_date(page, 2023, 2) == "2025-06-12"
    assert egrid_release_date(page, 2023, 0) == "2025-01-15"
    assert egrid_release_date(page, 2024, 2) is None
    assert egrid_release_date(page, 2023, 3) is None
