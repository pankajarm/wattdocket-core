"""INL's historical C3 meter series, preserving units, gaps and clock labels.

The publisher workbook is Strict OOXML, which openpyxl currently drops without
reading its worksheet. Read its small, explicitly reviewed XML schema directly.
The column's prose label says Energy, but both its cell style and chart axis
specify kW. No energy integral, facility-total boundary or live demand is inferred.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .facility_disclosure_identity import _capture_identity_evidence
from .models import Fetcher, SourceResult

SOURCE_ID = "inl_c3_power"
SITE_ID = "inl-c3"
SERIES_ID = "inl-c3-hpc-meter-power"
METADATA_URL = "https://nrds.inl.gov/api/3/action/package_show?id=c3_datacenter_hourly_power_usage"
LANDING_URL = "https://nrds.inl.gov/dataset/c3_datacenter_hourly_power_usage"
PACKAGE_ID = "859817f3-7b9b-4d8f-bffe-aabe59b6eade"
RESOURCE_ID = "71f8692c-5290-47ea-b0b0-37595d662d00"
WORKBOOK_URL = ("https://nrds.inl.gov/dataset/" + PACKAGE_ID + "/resource/" + RESOURCE_ID
                + "/download/c3_datacenter_hourly_power_usage_july2020-jan2026.xlsx")
HEADER = "IF-692 IF-692 High Performance Computing Center DC HPC Energy"
NS = {"s": "http://purl.oclc.org/ooxml/spreadsheetml/main"}
UTC = timezone.utc
MAX_ROWS = 100_000
MAX_EXPANDED_BYTES = 25_000_000
BOUNDARY = ("Publisher-defined IF-692 DC HPC meter. The release does not specify whether this meter includes "
            "cooling, other building loads, or only IT equipment; a whole-facility total is not established.")
METHOD = ("Publisher-reported hourly C3 data-center power. The column heading uses the word Energy, but each "
          "numeric cell and the chart y-axis explicitly use kW; values remain power, not kWh. "
          "MST/MDT labels are read from each timestamp cell's number format. Sub-millisecond spreadsheet "
          "round-off is normalized to the nearest hour, with original timestamps retained. The publisher does "
          "not specify whether timestamps mark interval start/end or whether values are hourly averages or "
          "instantaneous samples. Missing cells remain null. Historical snapshot through January 2026; "
          "no live telemetry, annual-to-hourly estimate, or whole-facility allocation.")

EVIDENCE = (
    {"id": "inl-c3-building-identity", "url": "https://www.energy.gov/sites/default/files/2021-04/CX-023164.pdf",
     "publisher": "U.S. Department of Energy", "kind": "pdf", "page": 1, "published_at": "2021-02-03",
     "expected_sha256": "ac047f9ee1cfa8715028f6fd60ff54aa60552bd4dc4a669786e33667f5b22394",
     "excerpts": ["IF-692, Collaborative Computing Center (C3)"]},
    {"id": "inl-c3-physical-address", "url": "https://ndlegis.gov/assembly/69-2025/interim/27-5137-01000-meeting-notice.pdf",
     "publisher": "North Dakota Legislative Council", "kind": "pdf", "page": 1, "published_at": "2026-04-15",
     "expected_sha256": "5e0f44a5344c8b2b5f3b8085d36004470dcd873f714d89cbde7f96158b6ae75e",
     "excerpts": ["Collaborative Computing Center, Idaho National Laboratory, 955 MK Simpson Boulevard, Idaho Falls, ID 83415"]},
)


class InlPowerError(ValueError):
    pass


def _require(ok, message):
    if not ok:
        raise InlPowerError(message)


def _stamp(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _xml(raw):
    _require(b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(), "OOXML entity declarations are not accepted")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise InlPowerError("Invalid INL workbook XML") from exc


def _cell_value(cell, strings):
    _require(cell.find("s:f", NS) is None, "INL meter cells must not be formula estimates")
    element = cell.find("s:v", NS)
    if element is None or element.text is None:
        return None
    value = element.text
    if cell.get("t") == "s":
        _require(value.isdigit() and int(value) < len(strings), "Invalid shared-string reference")
        return strings[int(value)]
    _require(cell.get("t") in (None, "n", "d"), "Unexpected INL cell representation")
    return value


def parse_inl_workbook(raw: bytes, *, now: datetime) -> dict:
    """Return publisher rows; timezone suffixes are evidence, never a guess."""
    _require(0 < len(raw) <= 8_000_000, "INL workbook exceeds bounded size")
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            _require(len(members) <= 50 and len({m.filename for m in members}) == len(members), "Unexpected INL workbook archive")
            _require(sum(m.file_size for m in members) <= MAX_EXPANDED_BYTES, "INL workbook expanded size exceeds bound")
            workbook = _xml(archive.read("xl/workbook.xml"))
            sheets = workbook.find("s:sheets", NS)
            _require(sheets is not None and len(sheets) == 1 and sheets[0].get("name") == "Power", "INL Power sheet changed")
            _require(workbook.get("conformance") == "strict", "INL Strict OOXML format changed")
            relationships = _xml(archive.read("xl/_rels/workbook.xml.rels"))
            sheet_id = sheets[0].get("{http://purl.oclc.org/ooxml/officeDocument/relationships}id")
            targets = [r.get("Target") for r in relationships if r.get("Id") == sheet_id]
            _require(targets == ["worksheets/sheet1.xml"], "INL worksheet relationship changed")
            strings = ["".join(e.itertext()) for e in _xml(archive.read("xl/sharedStrings.xml"))]
            styles = _xml(archive.read("xl/styles.xml"))
            formats = {int(e.get("numFmtId")): e.get("formatCode") for e in styles.find("s:numFmts", NS)}
            style_formats = [formats.get(int(e.get("numFmtId"))) for e in styles.find("s:cellXfs", NS)]
            chart = _xml(archive.read("xl/charts/chart1.xml"))
            chart_formats = [e.text for e in chart.iter() if e.tag.endswith("}formatCode")]
            _require('0.0"kW"' in chart_formats, "INL chart no longer establishes kW power units")
            sheet = _xml(archive.read("xl/worksheets/sheet1.xml"))
    except (BadZipFile, KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, InlPowerError):
            raise
        raise InlPowerError("Unsupported or malformed INL power workbook") from exc
    rows = sheet.find("s:sheetData", NS)
    _require(rows is not None and 1 < len(rows) <= MAX_ROWS + 1, "INL worksheet row count outside bound")
    header = {_cell.get("r"): _cell_value(_cell, strings) for _cell in rows[0]}
    _require(header == {"A1": "Timestamp", "B1": HEADER}, "INL meter identity/header changed")
    readings, previous, missing, normalized_count, row_ids = [], None, 0, 0, set()
    for row in list(rows)[1:]:
        row_id = row.get("r")
        _require(row_id and row_id.isdigit() and 1 < int(row_id) <= MAX_ROWS + 2 and row_id not in row_ids,
                 "Invalid or duplicate INL worksheet row")
        row_ids.add(row_id)
        cells = {cell.get("r"): cell for cell in row}
        _require(len(cells) == len(row) and set(cells) <= {"A" + row_id, "B" + row_id}, "Unexpected INL row columns")
        a, b = cells.get("A" + row_id), cells.get("B" + row_id)
        _require(a is not None and a.get("t") == "d", "INL timestamp cell lacks its explicit date type")
        source_timestamp = _cell_value(a, strings)
        try:
            date_format = style_formats[int(a.get("s"))]
            clock = "MDT" if date_format and '"MDT"' in date_format else "MST" if date_format and '"MST"' in date_format else None
            _require(clock is not None, "INL timestamp lacks an explicit reviewed MST/MDT clock")
            local = datetime.fromisoformat(source_timestamp)
            _require(local.tzinfo is None, "INL date value unexpectedly contains another timezone")
        except (TypeError, ValueError, IndexError) as exc:
            if isinstance(exc, InlPowerError):
                raise
            raise InlPowerError("Invalid INL source timestamp or style") from exc
        floor = local.replace(minute=0, second=0, microsecond=0)
        nearest = floor + timedelta(hours=1) if (local - floor).total_seconds() >= 1800 else floor
        _require(abs((nearest - local).total_seconds()) < .001, "INL timestamp is outside hourly rounding tolerance")
        normalized_count += local != nearest
        timestamp = nearest.replace(tzinfo=timezone(timedelta(hours=-6 if clock == "MDT" else -7))).astimezone(UTC)
        _require(timestamp <= now.astimezone(UTC) and timestamp.year >= 2020, "INL timestamp outside observed historical range")
        _require(previous is None or timestamp - previous == timedelta(hours=1), "INL timestamps duplicate, reverse or omit an hourly row")
        previous = timestamp
        value = _cell_value(b, strings) if b is not None else None
        if value in (None, ""):
            value = None
            missing += 1
        else:
            try:
                _require(style_formats[int(b.get("s"))] == '0.0"kW"', "INL numeric cell lost its kW unit")
                value = float(value)
            except (ValueError, TypeError, IndexError) as exc:
                if isinstance(exc, InlPowerError):
                    raise
                raise InlPowerError("Invalid INL power value/style") from exc
            _require(math.isfinite(value) and value >= 0, "INL power must be finite and nonnegative")
        readings.append([_stamp(timestamp), value, f"Power!A{row_id}:B{row_id}", source_timestamp, clock])
    observed = [r for r in readings if r[1] is not None]
    _require(observed, "INL workbook has no measured power values")
    return {"readings": readings, "missing_count": missing, "observed_start": observed[0][0],
            "observed_end": observed[-1][0], "record_count": len(observed),
            "validation": {"source_rows": len(readings), "numeric_rows": len(observed), "blank_rows": missing,
                           "unique_utc_hours": True, "continuous_hourly_labels": True,
                           "timestamp_rounding_tolerance_seconds": .001,
                           "timestamps_with_submillisecond_noise": normalized_count,
                           "unit_evidence": 'xl/styles.xml numFmt 0.0"kW" and xl/charts/chart1.xml y-axis',
                           "header_retained": HEADER}}


def collect_inl_c3_power(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    metadata_raw = fetcher.get_bytes(METADATA_URL)
    _require(len(metadata_raw) <= 1_000_000, "INL catalog metadata exceeds bound")
    metadata = json.loads(metadata_raw)
    result = metadata.get("result") or {}
    _require(metadata.get("success") is True and result.get("id") == PACKAGE_ID and
             result.get("title") == "C3 Datacenter Hourly Power Usage" and result.get("private") is False,
             "INL dataset identity/access changed")
    resources = result.get("resources", [])
    _require(len(resources) == 1 and resources[0].get("id") == RESOURCE_ID and
             resources[0].get("url") == WORKBOOK_URL and resources[0].get("state") == "active",
             "INL source resource/edition changed; review the new workbook")
    # The document cache cannot silently reuse an old file after the catalog
    # announces a replacement under the same URL. Review that edition first.
    _require(resources[0].get("last_modified") == "2026-02-12T15:47:20.669047",
             "INL resource revision changed; refresh and review the new workbook")
    _require(result.get("license_title") == "Creative Commons Attribution", "INL redistribution license changed")
    raw = fetcher.get_bytes(WORKBOOK_URL)
    parsed = parse_inl_workbook(raw, now=now)
    evidence = [_capture_identity_evidence(fetcher, spec, _stamp(now)) for spec in EVIDENCE]
    identity = [{"url": e["url"], "sha256": e["content_sha256"], "locator": f"PDF page {e['page']}",
                 "quote": " ".join(e["excerpts"]), "publisher": e["publisher"], "published_at": e["published_at"]}
                for e in evidence]
    series = {"series_id": SERIES_ID, "site_id": SITE_ID, "label": "INL C3 reported HPC meter power",
              "metric": "electricity_power", "unit": "kW", "measurement_basis": "publisher_reported_meter_power",
              "measurement_boundary": BOUNDARY, "whole_facility": False, "interval_minutes": 60,
              "time_basis": "Per-cell publisher MST (UTC−07:00) / MDT (UTC−06:00); interval start/end and averaging convention unspecified",
              "columns": ["timestamp_utc", "value", "source_row", "source_timestamp", "source_timezone"],
              **{k: parsed[k] for k in ("readings", "observed_start", "observed_end", "missing_count")},
              "source_url": WORKBOOK_URL, "source_sha256": hashlib.sha256(raw).hexdigest(),
              "source_locator": "Power worksheet, columns A:B; kW number format and chart y-axis",
              "license": result["license_title"], "methodology_note": METHOD, "historical_snapshot": True,
              "source_header": HEADER, "validation": parsed["validation"],
              "publisher": "Idaho National Laboratory / Nuclear Research Data System"}
    site = {"site_id": SITE_ID, "name": "INL Collaborative Computing Center (C3)",
            "operator": "Idaho National Laboratory", "address": {"street": "955 MK Simpson Boulevard",
            "city": "Idaho Falls", "state": "ID", "country": "US"}, "reporting_boundary": BOUNDARY,
            "identity_evidence": identity, "inventory_record_ids": [], "source_identifiers": {"inl_building": "IF-692"}}
    return SourceResult(source_id=SOURCE_ID, label="INL C3 historical hourly meter power", url=LANDING_URL,
        data={"power_sites": [site], "power_series": [series], "excluded": [],
              "catalog": {"url": METADATA_URL, "sha256": hashlib.sha256(metadata_raw).hexdigest(),
                          "metadata_modified": result.get("metadata_modified"),
                          "resource_last_modified": resources[0].get("last_modified"), "doi": "10.48806/3018497"}},
        record_count=parsed["record_count"], observation_start=parsed["observed_start"], observation_end=parsed["observed_end"],
        published_at="2026-02-12", data_vintage="July 2020–January 2026 historical hourly readings",
        coverage="One C3 HPC meter series at an independently identified INL building; no inventory allocation",
        note=METHOD)
