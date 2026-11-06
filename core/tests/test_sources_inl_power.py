"""Real Strict-OOXML excerpts: units, missingness, local clocks and site proof."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from wattdocket.production.sources_inl_power import (
    EVIDENCE, HEADER, METADATA_URL, NS, WORKBOOK_URL, InlPowerError,
    collect_inl_c3_power, parse_inl_workbook,
)

FIXTURE = Path(__file__).parent / "fixtures" / "inl_power"
EXCERPT = json.loads((FIXTURE / "excerpt.json").read_text())
NOW = datetime(2026, 11, 4, 23, tzinfo=timezone.utc)
MAIN_NS = NS["s"]
REL_NS = "http://purl.oclc.org/ooxml/officeDocument/relationships"


def workbook(chunk="first", *, mutate=None):
    """Retain exact source cells/styles; omit unrelated drawings and chart cache."""
    sheet = ET.Element("{" + MAIN_NS + "}worksheet")
    data = ET.SubElement(sheet, "{" + MAIN_NS + "}sheetData")
    data.append(ET.fromstring(EXCERPT["header_xml"]))
    for row in EXCERPT["chunks"][chunk]:
        data.append(ET.fromstring(row))
    if mutate:
        mutate(data)
    parts = {
        "xl/workbook.xml": f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}" conformance="strict"><sheets><sheet name="Power" sheetId="1" r:id="rId1"/></sheets></workbook>'.encode(),
        "xl/_rels/workbook.xml.rels": f'<Relationships><Relationship Id="rId1" Type="{REL_NS}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'.encode(),
        "xl/sharedStrings.xml": EXCERPT["strings_xml"].encode(),
        "xl/styles.xml": EXCERPT["styles_xml"].encode(),
        "xl/worksheets/sheet1.xml": ET.tostring(sheet),
        "xl/charts/chart1.xml": b'<c:chart xmlns:c="http://purl.oclc.org/ooxml/drawingml/chart"><c:formatCode>0.0"kW"</c:formatCode></c:chart>',
    }
    target = io.BytesIO()
    with ZipFile(target, "w") as archive:
        for path, raw in parts.items():
            archive.writestr(path, raw)
    return target.getvalue()


def set_value(rows, value):
    cell = rows[1][1]
    cell.find("s:v", NS).text = value


def test_real_values_remain_kw_and_raw_timestamp_noise_is_auditable():
    result = parse_inl_workbook(workbook(), now=NOW)
    assert result["readings"][0] == ["2020-07-18T15:00:00Z", 1448.7110595703125,
                                      "Power!A2:B2", "2020-07-18T09:00:00.000", "MDT"]
    assert result["readings"][1][0] == "2020-07-18T16:00:00Z"
    assert result["readings"][1][3] == "2020-07-18T09:59:59.99999979045242400"
    assert result["record_count"] == 6
    assert result["missing_count"] == 0
    assert result["validation"]["timestamps_with_submillisecond_noise"] == 2
    assert result["validation"]["header_retained"] == HEADER


@pytest.mark.parametrize("chunk", ["fall", "spring"])
def test_real_dst_transition_rows_keep_unique_contiguous_utc_hours(chunk):
    result = parse_inl_workbook(workbook(chunk), now=NOW)
    stamps = [datetime.fromisoformat(row[0].replace("Z", "+00:00")) for row in result["readings"]]
    assert len(stamps) == len(set(stamps)) == 6
    assert all((b - a).total_seconds() == 3600 for a, b in zip(stamps, stamps[1:]))
    assert {row[4] for row in result["readings"]} == {"MST", "MDT"}


def test_real_blank_cells_remain_missing_not_zero_and_reported_zero_survives():
    result = parse_inl_workbook(workbook("missing"), now=NOW)
    assert result["missing_count"] > 0
    assert sum(row[1] is None for row in result["readings"]) == result["missing_count"]
    zero = parse_inl_workbook(workbook(mutate=lambda rows: set_value(rows, "0")), now=NOW)
    assert zero["readings"][0][1] == 0
    assert zero["missing_count"] == 0


@pytest.mark.parametrize("value", ["NaN", "inf", "-1", "not-a-number"])
def test_invalid_meter_numbers_refuse(value):
    with pytest.raises(InlPowerError):
        parse_inl_workbook(workbook(mutate=lambda rows: set_value(rows, value)), now=NOW)


@pytest.mark.parametrize("damage", ["formula", "duplicate_time", "missing_hour", "wrong_unit", "no_timezone", "off_hour", "wrong_header"])
def test_scope_units_timestamps_and_formulas_fail_closed(damage):
    def mutate(rows):
        if damage == "formula":
            ET.SubElement(rows[1][1], "{" + MAIN_NS + "}f").text = "8760/100"
        elif damage == "duplicate_time":
            rows[2][0].find("s:v", NS).text = rows[1][0].find("s:v", NS).text
        elif damage == "missing_hour":
            rows.remove(rows[2])
        elif damage == "wrong_unit":
            rows[1][1].set("s", "0")
        elif damage == "no_timezone":
            rows[1][0].set("s", "0")
        elif damage == "off_hour":
            rows[1][0].find("s:v", NS).text = "2020-07-18T09:30:00.000"
        else:
            rows[0][1].find("s:v", NS).text = "0"
    with pytest.raises(InlPowerError):
        parse_inl_workbook(workbook(mutate=mutate), now=NOW)


def test_changed_chart_unit_is_not_assumed_from_numeric_magnitude():
    original = workbook()
    target = io.BytesIO()
    with ZipFile(io.BytesIO(original)) as source, ZipFile(target, "w") as out:
        for name in source.namelist():
            raw = source.read(name)
            if name == "xl/charts/chart1.xml":
                raw = raw.replace(b'"kW"', b'"kWh"')
            out.writestr(name, raw)
    with pytest.raises(InlPowerError, match="kW power units"):
        parse_inl_workbook(target.getvalue(), now=NOW)


class CapturedFetcher:
    def __init__(self, metadata=None):
        self.values = {
            METADATA_URL: json.dumps(metadata or json.loads((FIXTURE / "metadata.json").read_text())).encode(),
            WORKBOOK_URL: workbook(),
            EVIDENCE[0]["url"]: (FIXTURE / "c3-building.pdf").read_bytes(),
            EVIDENCE[1]["url"]: (FIXTURE / "c3-location.pdf").read_bytes(),
        }
        self.calls, self.receipts = [], {}

    def get_bytes(self, url):
        self.calls.append(url)
        raw = self.values[url]
        sha = hashlib.sha256(raw).hexdigest()
        self.receipts[url] = {"url": url, "sha256": sha, "raw_key": "raw/" + sha + ".gz",
                              "retrieved_at": "2026-11-04T22:31:27Z"}
        return raw


def test_collector_preserves_physical_site_proof_without_inventory_or_facility_total_claim():
    fetcher = CapturedFetcher()
    result = collect_inl_c3_power(fetcher, now=NOW)
    assert result.source_id == "inl_c3_power"
    site = result.data["power_sites"][0]
    assert site["address"]["street"] == "955 MK Simpson Boulevard"
    assert site["source_identifiers"] == {"inl_building": "IF-692"}
    assert site["inventory_record_ids"] == []
    assert len(site["identity_evidence"]) == 2
    assert all(fetcher.receipts[e["url"]]["sha256"] == e["sha256"] for e in site["identity_evidence"])
    series = result.data["power_series"][0]
    assert series["whole_facility"] is False
    assert series["historical_snapshot"] is True
    assert series["unit"] == "kW"
    assert "averages or instantaneous" in series["methodology_note"]
    assert "Energy" in series["methodology_note"]
    assert result.published_at == "2026-02-12"
    assert len(fetcher.calls) == 4


@pytest.mark.parametrize("damage", ["revision", "new_resource", "license", "identity"])
def test_catalog_changes_cannot_silently_reuse_cached_old_workbook(damage):
    metadata = deepcopy(json.loads((FIXTURE / "metadata.json").read_text()))
    source = metadata["result"]
    if damage == "revision":
        source["resources"][0]["last_modified"] = "2026-11-04T22:00:00"
    elif damage == "new_resource":
        source["resources"].append(deepcopy(source["resources"][0]))
    elif damage == "license":
        source["license_title"] = "All rights reserved"
    else:
        source["title"] = "Modeled data-center EnergyPlus energy"
    fetcher = CapturedFetcher(metadata)
    with pytest.raises(InlPowerError):
        collect_inl_c3_power(fetcher, now=NOW)
    assert fetcher.calls == [METADATA_URL]


def test_changed_primary_site_proof_is_rejected():
    fetcher = CapturedFetcher()
    fetcher.values[EVIDENCE[1]["url"]] += b" changed"
    with pytest.raises(ValueError, match="document changed"):
        collect_inl_c3_power(fetcher, now=NOW)
