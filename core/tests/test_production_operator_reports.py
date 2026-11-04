"""Operator locations must retain scope, numeric meaning and uncertainty."""
import hashlib
from datetime import datetime, timezone

import pymupdf
import pytest

from wattdocket.production.sources_grid import GridSourceError
from wattdocket.production import sources_operator_reports as reports


def table_text():
    lines = list(reports.TABLE_HEADER)
    for name in reports.LOCATIONS + reports.AGGREGATES:
        lines.append(name + (str(reports.FOOTNOTES[name]) if name in reports.FOOTNOTES else ""))
        components = reports.COMPONENTS.get(name, ())
        lines += list(components)
        lines.append("Million gallons")
        withdrawal, discharge, consumption = (123, 41, 82) if name == "Data centers total" else (3, 1, 2)
        lines.append(str(withdrawal))
        lines += [str(withdrawal / len(components))] * len(components) if components else []
        lines += [str(discharge), str(consumption), "<1"]
    return "\n".join(lines + ["Legend", "Assurance footer"])


METHODS = ("actual metered or invoiced data; estimate water withdrawal; estimate water discharge; "
           "subtracting water discharge from water withdrawal; exclude seawater and brackish water")
FOOTNOTES = "Unless otherwise specified, water withdrawals are potable water"


def pdf_bytes(*, methods=METHODS, pages=117):
    document = pymupdf.open()
    for index in range(pages):
        document.new_page(width=1000, height=5000 if index == 96 else 842)
    document[86].insert_text((36, 36), methods, fontsize=6)
    document[96].insert_text((36, 36), table_text(), fontsize=6)
    document[115].insert_text((36, 36), FOOTNOTES, fontsize=6)
    raw = document.tobytes()
    document.close()
    return raw


class Fetcher:
    def __init__(self, raw):
        self.values = {reports.REPORT_PAGE: ('<h1>2026 Environmental Report</h1><p>June 2026</p>'
                       '<a href="' + reports.PDF_URL + '">Download</a>').encode(), reports.PDF_URL: raw}

    def get_bytes(self, url):
        return self.values[url]


def test_locations_and_aggregates_keep_different_scopes_and_no_facility_join():
    rows, aggregates, validation = reports.parse_google_water_table(table_text())
    assert len(rows) == 40 and len(aggregates) == 2
    assert rows[0]["withdrawal_million_gallons"] == 3
    assert rows[0]["discharge_million_gallons"] == 1
    assert rows[0]["consumption_million_gallons"] == 2
    assert rows[0]["unit"] == "million gallons"
    assert rows[0]["measurement_basis"] == "operator reported; may include estimates"
    assert rows[0]["facility_binding_verified"] is False
    assert all(row["reporting_scope"] == "named operator location" for row in rows)
    assert all(row["reporting_scope"] == "operator aggregate" for row in aggregates)
    assert aggregates[0]["limited_assurance_marked"] is False
    assert validation["facility_bindings"] == 0
    assert validation["max_total_rounding_difference_million_gallons"] == 0
    assert next(row for row in rows if row["location_label"] == "Dublin, Ireland")["air_cooled_footnote"] is True


@pytest.mark.parametrize("problem", ["year", "units", "balance", "total", "missing", "duplicate", "columns", "nonfinite"])
def test_changed_schema_coverage_units_or_arithmetic_fail_closed(problem):
    text = table_text()
    if problem == "year":
        text = text.replace("2025", "2026", 1)
    elif problem == "units":
        text = text.replace("Million gallons", "Megaliters", 1)
    elif problem == "balance":
        text = text.replace("Million gallons\n3\n1\n2", "Million gallons\n3\n1\n9", 1)
    elif problem == "total":
        text = text.replace("Million gallons\n123\n41.0\n41.0\n41.0\n41\n82", "Million gallons\n153\n51\n51\n51\n51\n102")
    elif problem == "missing":
        text = text.replace("Ashburn, VA\nMillion gallons\n3\n1\n2\n<1\n", "")
    elif problem == "duplicate":
        text = text.replace("Berkeley County, SC", "Ashburn, VA")
    elif problem == "columns":
        text = text.replace("Discharge\nConsumption", "Consumption\nDischarge", 1)
    else:
        text = text.replace("Million gallons\n3", "Million gallons\nNaN", 1)
    with pytest.raises(GridSourceError):
        reports.parse_google_water_table(text)


def test_collector_extracts_real_pdf_and_retains_dates_hash_method_and_scope(monkeypatch):
    raw = pdf_bytes()
    monkeypatch.setattr(reports, "PDF_SHA256", hashlib.sha256(raw).hexdigest())
    result = reports.collect_google_water_disclosures(Fetcher(raw), now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    assert result.source_id == "google_water2025" and result.record_count == 40
    assert result.published_at is None
    assert result.observation_start == "2025-01-01T00:00:00Z"
    assert result.observation_end == "2026-01-01T00:00:00Z"
    assert result.data["source"]["publication_month"] == "2026-06"
    assert result.data["source"]["pdf_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result.data["observations"][0]["source_page"] == 97
    assert result.data["observations"][0]["methodology_pages"] == [87, 88]
    assert "may include estimates" in result.data["observations"][0]["measurement_basis"]


@pytest.mark.parametrize("problem", ["checksum", "link", "method", "pages"])
def test_report_or_method_changes_require_new_review(monkeypatch, problem):
    raw = pdf_bytes(methods="no estimation disclosure" if problem == "method" else METHODS,
                    pages=118 if problem == "pages" else 117)
    monkeypatch.setattr(reports, "PDF_SHA256", hashlib.sha256(raw).hexdigest())
    fetcher = Fetcher(raw)
    if problem == "checksum":
        fetcher.values[reports.PDF_URL] += b"changed"
    elif problem == "link":
        fetcher.values[reports.REPORT_PAGE] = b'<h1>2026 Environmental Report</h1><p>June 2026</p><a href="https://elsewhere.example/report.pdf">Download</a>'
    with pytest.raises(GridSourceError):
        reports.collect_google_water_disclosures(fetcher, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
