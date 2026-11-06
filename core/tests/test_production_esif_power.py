"""Boundary, offset, coverage and raw-evidence guards for measured ESIF power."""
import csv
import io
import math
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
import pytest

from wattdocket.production import sources_esif_power as esif


def native(time, daylight=False):
    return time.strftime("%a %b %d %Y %H:%M:%S") + (
        " GMT-0600 (Mountain Daylight Time)" if daylight else " GMT-0700 (Mountain Standard Time)")


def hour(time=datetime(2025, 1, 1), *, omit=(), value=100, daylight=False):
    return [(native(time + timedelta(minutes=minute), daylight), value) for minute in range(60) if minute not in omit]


def archive(readings, *, header=None, member=None):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(header or esif.HEADER)
    for timestamp, value in readings:
        writer.writerow(["", timestamp, 900, "", "", 800, value, 700, 1.2, 600, 1])
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member or esif.MEMBER, stream.getvalue())
        z.writestr("__MACOSX/._" + esif.MEMBER, b"harmless metadata")
    return out.getvalue()


def test_selects_latest_observed_year_and_only_it_power_without_energy_conversion():
    readings = hour(datetime(2024, 1, 1), value=999) + hour(value=100)
    readings[60] = (readings[60][0], 160)
    result = esif.parse_power_archive(archive(readings))
    assert result["selected_year"] == 2025
    assert [r["year"] for r in result["available_years"]] == [2024, 2025]
    assert result["readings"][0][0:2] == ["2025-01-01T07:00:00Z", 101]
    assert result["readings"][0][5:] == [60, 60, 6060, 0]
    assert result["missing_count"] == 0
    assert result["native_sampling_mode_seconds"] == 60


def test_preserves_fall_back_as_two_distinct_utc_hours():
    time = datetime(2025, 11, 2, 1)
    result = esif.parse_power_archive(archive(hour(time, daylight=True, value=100) + hour(time, value=200)))
    assert [(row[0], row[1]) for row in result["readings"]] == [
        ("2025-11-02T07:00:00Z", 100), ("2025-11-02T08:00:00Z", 200)]
    assert "GMT-0600" in result["readings"][0][4]
    assert "GMT-0700" in result["readings"][1][4]


def test_spring_clock_change_does_not_create_a_fake_missing_utc_hour():
    result = esif.parse_power_archive(archive(hour(datetime(2025, 3, 9, 1)) +
                                            hour(datetime(2025, 3, 9, 3), daylight=True)))
    assert len(result["readings"]) == 2
    assert result["missing_count"] == 0


@pytest.mark.parametrize("omitted,expected", [({10, 11, 12, 13}, 100),
                                            ({10, 11, 12, 13, 14}, None),
                                            (set(range(5)), 100),
                                            (set(range(6)), None),
                                            (set(range(55, 60)), None),
                                            (set(range(56, 60)), 100)])
def test_hour_edges_and_internal_gaps_must_not_exceed_five_minutes(omitted, expected):
    result = esif.parse_power_archive(archive(hour(omit=omitted) + hour(datetime(2025, 1, 1, 1))))
    assert result["readings"][0][1] == expected
    assert result["readings"][0][5] == 60 - len(omitted)
    assert result["missing_count"] == (1 if expected is None else 0)


def test_missing_numeric_values_are_not_zero_and_low_count_is_withheld():
    rows = hour()
    for i in range(0, 60, 3):
        rows[i] = (rows[i][0], "")
    result = esif.parse_power_archive(archive(rows + hour(datetime(2025, 1, 1, 1))))
    assert result["readings"][0][1] is None
    assert result["readings"][0][5] == 40
    assert result["native_numeric_sample_count"] == 100


def test_fully_absent_hour_is_published_as_null_with_coverage_evidence():
    result = esif.parse_power_archive(archive(hour() + hour(datetime(2025, 1, 1, 2))))
    assert result["readings"][1] == ["2025-01-01T08:00:00Z", None, None, None, None, 0, 3600, 0, 0]
    assert result["missing_count"] == 1


@pytest.mark.parametrize("value", ["NaN", "Infinity", -1, 100001, "N/A"])
def test_non_numeric_nonfinite_negative_and_unreviewed_extreme_power_fail_closed(value):
    rows = hour()
    rows[10] = (rows[10][0], value)
    with pytest.raises(ValueError):
        esif.parse_power_archive(archive(rows))


def test_conflicting_duplicate_timestamp_withholds_hour_instead_of_inflating_coverage():
    rows = hour()
    rows.insert(10, (rows[9][0], 999))
    result = esif.parse_power_archive(archive(rows + hour(datetime(2025, 1, 1, 1))))
    assert result["readings"][0][1] is None
    assert result["readings"][0][5] == 59
    assert result["readings"][0][7:] == [5900, 1]
    assert result["available_years"][0]["duplicate_timestamp_rows"] == 1
    assert result["withheld_hour_reasons"] == {"duplicate_publisher_timestamp": 1}


@pytest.mark.parametrize("mutation", ["reverse", "name_offset", "no_offset", "weekday", "old_year"])
def test_invalid_order_or_unresolved_time_fails_closed(mutation):
    rows = hour()
    if mutation == "reverse":
        rows[9], rows[10] = rows[10], rows[9]
    elif mutation == "name_offset":
        rows[10] = (rows[10][0].replace("GMT-0700", "GMT-0600"), 100)
    elif mutation == "no_offset":
        rows[10] = (rows[10][0].split(" GMT")[0], 100)
    elif mutation == "weekday":
        rows[10] = (rows[10][0].replace("Wed", "Mon"), 100)
    else:
        rows += hour(datetime(2024, 1, 1))
    with pytest.raises(ValueError):
        esif.parse_power_archive(archive(rows))


def test_header_units_unknown_member_and_expansion_budget_fail_closed(monkeypatch):
    header = list(esif.HEADER)
    header[6] = "it_energy_kwh"
    with pytest.raises(ValueError, match="fields or units"):
        esif.parse_power_archive(archive(hour(), header=header))
    with pytest.raises(ValueError, match="member schema"):
        esif.parse_power_archive(archive(hour(), member="unknown.csv"))
    monkeypatch.setattr(esif, "MAX_EXPANDED_BYTES", 50)
    with pytest.raises(ValueError, match="expanded-size"):
        esif.parse_power_archive(archive(hour()))


def notice_bytes():
    return Path(__file__).with_name("fixtures").joinpath("esif-license-notice.txt").read_bytes()


def test_complete_custom_license_is_retained_and_changed_terms_require_review():
    notice = notice_bytes()
    parsed = esif.parse_license(notice)
    assert parsed["notice"] == notice.decode().strip()
    assert parsed["requires_full_notice_with_copies"]
    assert "Creative Commons" not in parsed["name"]
    with pytest.raises(ValueError, match="terms changed"):
        esif.parse_license(notice.replace(b"without any fee or cost", b"without any fee or cost for noncommercial use only"))


class Replay:
    def __init__(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((30, 40), "Energy Systems Integration Facility\n15257 Denver West Parkway, Golden, CO 80401")
            address = pdf.tobytes()
        self.docs = {
            esif.PAGE_URL: ("HPC Facility Power Usage Effectiveness 10.7799/3015212 Status Public "
                            "Last Updated 01/29/2026 " + esif.ZIP_URL).encode(),
            esif.README_URL: b"it_power_kw: IT equipment (kilowatts) IT equipment on the data center floor",
            esif.METHOD_URL: b"All of the above are measured with electrical power meters except for: Generator heat tower filter pump IT equipment",
            esif.FACILITY_URL: b"High-Performance Computing Located in the Energy Systems Integration Facility (ESIF)",
            esif.ADDRESS_URL: address, esif.LICENSE_URL: notice_bytes(), esif.ZIP_URL: archive(hour()),
        }

    def get_bytes(self, url):
        return self.docs[url]


def test_collector_contract_preserves_raw_hash_site_boundary_license_and_historical_time():
    fetcher = Replay()
    result = esif.collect_esif_power(fetcher, now=datetime(2026, 11, 4, tzinfo=timezone.utc))
    assert result.source_id == "nrel_esif_power" and result.record_count == 1
    assert result.published_at == "2026-01-29" and result.data_vintage == "2025"
    site = result.data["power_sites"][0]
    series = result.data["power_series"][0]
    assert site["inventory_record_ids"] == []
    assert site["address"]["street"] == "15257 Denver West Parkway"
    assert series["whole_facility"] is False and series["historical_snapshot"] is True
    assert series["source_sha256"] == esif._sha(fetcher.docs[esif.ZIP_URL])
    assert series["aggregation"]["annual_energy_derivable"] is False
    assert series["license"]["notice"] == notice_bytes().decode().strip()
    assert series["readings"][0][1] == 100
    assert "derived_hourly" in series["measurement_basis"]


@pytest.mark.parametrize("url", [esif.METHOD_URL, esif.FACILITY_URL, esif.README_URL, esif.PAGE_URL])
def test_collector_requires_primary_units_meter_boundary_and_site_proof(url):
    fetcher = Replay()
    fetcher.docs[url] = b"Information is currently unavailable"
    with pytest.raises(ValueError):
        esif.collect_esif_power(fetcher, now=datetime(2026, 11, 4, tzinfo=timezone.utc))


def test_future_meter_observations_cannot_be_published_as_actuals():
    fetcher = Replay()
    fetcher.docs[esif.ZIP_URL] = archive(hour(datetime(2027, 1, 1)))
    with pytest.raises(ValueError, match="observations are in the future"):
        esif.collect_esif_power(fetcher, now=datetime(2026, 11, 4, tzinfo=timezone.utc))
