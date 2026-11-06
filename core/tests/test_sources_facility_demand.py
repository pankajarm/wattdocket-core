"""Peak demand cannot inherit annual energy, requested capacity or meter coverage."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from wattdocket.production import sources_facility_demand as source

FIXTURES = Path(__file__).parent / "fixtures/facility_demand"
NOW = datetime(2026, 11, 4, 22, 45, tzinfo=timezone.utc)


class NebraskaCapture:
    def __init__(self):
        self.index = json.loads((FIXTURES / "nebraska-index.json").read_text())
        self.receipts = json.loads((FIXTURES / "nebraska-receipts.json").read_text())
        self.values = {}
        selected, _ = source._nebraska_index(self.index, now=NOW)
        for fid, item in selected.items():
            self.values[item["url"]] = (FIXTURES / f"nebraska-{fid}.pdf").read_bytes()

    def post_json(self, url, payload):
        assert url == source.NE_INDEX and payload == source.NE_QUERY
        return copy.deepcopy(self.index)

    def get_bytes(self, url):
        return self.values[url]

    def receipt(self, url):
        return self.receipts.get(url)


def nyc_rows():
    saved = json.loads((FIXTURES / "nyc-demand.json").read_text())
    return [(row, url, item["receipt"]["sha256"]) for url, item in saved.items() for row in json.loads(item["raw_text"])]


def positive_nyc():
    return next(x for x in nyc_rows() if x[0]["property_id"] == "21355509")


def test_actual_unredacted_nebraska_pdf_bytes_publish_three_distinct_reported_peaks():
    fetcher = NebraskaCapture()
    result = source.collect_nebraska_facility_demand(fetcher, now=NOW)
    assert result.source_id == "nebraska_facility_demand" and result.record_count == 3
    assert result.data["capture"]["indexed_documents"] == 25
    assert result.data["capture"]["annual_reporting_facilities"] == 6
    assert len(result.data["excluded"]) == 3
    peaks = {d["site_id"]: d for d in result.data["power_demands"]}
    assert {key: row["value"] for key, row in peaks.items()} == {"ne-dcr:106518": 254.5, "ne-dcr:90045": 52.65, "ne-dcr:114235": 99}
    for row in peaks.values():
        assert row["unit"] == "MW" and row["reporting_year"] == 2025
        assert row["measurement_boundary"] == "reported_campus_maximum"
        assert row["reporting_scope"] == "campus" and row["whole_facility"] is True
        assert row["interval_minutes"] is None and row["peak_month"] is None
        assert row["is_meter_observation"] is False
        assert hashlib.sha256(fetcher.values[row["source_url"]]).hexdigest() == row["source_sha256"]
        assert row["public_access_evidence"]["redacted_values_used"] is False
    sites = {s["site_id"]: s for s in result.data["power_sites"]}
    assert sites["ne-dcr:90045"]["address"]["street"] == "5475 Cloud Court"
    assert sites["ne-dcr:106518"]["address"]["street"] == "14865 Gold Coast Road"
    assert sites["ne-dcr:114235"]["facility_type"] == "cryptocurrency_mining"
    assert all(s["inventory_record_ids"] == [] for s in sites.values())
    assert all(s["public_access_evidence"]["returned_to_public_file_at"] == "2026-11-02"
               for s in sites.values() if s["operator"] == "Google LLC")


def test_redacted_omaha_and_energy_only_returns_never_become_peak_readings(monkeypatch):
    import pymupdf
    original = pymupdf.open
    reviewed_hashes = {r["sha256"] for r in source.NE_REVIEWED.values()}
    def reviewed_only(*args, **kwargs):
        assert hashlib.sha256(kwargs["stream"]).hexdigest() in reviewed_hashes, "Do not extract hidden text from unreviewed/redacted reports"
        return original(*args, **kwargs)
    monkeypatch.setattr(pymupdf, "open", reviewed_only)
    result = source.collect_nebraska_facility_demand(NebraskaCapture(), now=NOW)
    excluded = {x["site_id"]: x["reason"] for x in result.data["excluded"]}
    assert "visibly redacts" in excluded["ne-dcr:117197"]
    assert "MWh" in excluded["ne-dcr:108432"]
    assert "no peak-demand" in excluded["ne-dcr:103198"]


@pytest.mark.parametrize("mutation", ["pdf_bytes", "receipt", "latest_date", "missing", "truncated", "column", "duplicate"])
def test_nebraska_changes_cannot_inherit_reviewed_peak_numbers(mutation):
    fetcher = NebraskaCapture()
    row = next(x for x in fetcher.index["Data"] if x["Name"].startswith("11/2/2026 - DCR - 106518"))
    url = source.NE_DOCUMENT_BASE + quote(row["ID"], safe="") + "/"
    if mutation == "pdf_bytes":
        raw = fetcher.values[url] + b"changed source"
        fetcher.values[url] = raw
        sha = hashlib.sha256(raw).hexdigest()
        fetcher.receipts[url].update(sha256=sha, raw_key=f"raw/{sha}.gz")
    elif mutation == "receipt": fetcher.receipts[url]["raw_key"] = "raw/unrelated.gz"
    elif mutation == "latest_date": row["DisplayColumnValues"][0]["Value"] = "11/3/2026"
    elif mutation == "missing":
        fetcher.index["Data"] = [x for x in fetcher.index["Data"] if x["DisplayColumnValues"][-1]["Value"] != "106518"]
    elif mutation == "truncated": fetcher.index["Truncated"] = True
    elif mutation == "column": fetcher.index["DisplayColumns"][-1]["Heading"] = "Other ID"
    else: fetcher.index["Data"].append(copy.deepcopy(row))
    with pytest.raises(source.FacilityDemandError): source.collect_nebraska_facility_demand(fetcher, now=NOW)


def test_public_query_receipt_cannot_be_an_unrelated_post_request():
    fetcher = NebraskaCapture()
    receipt = next(r for r in fetcher.receipts.values() if r.get("request_method") == "POST")
    receipt["request_body"]["Keywords"][0]["Value"] = "AIR"
    with pytest.raises(source.FacilityDemandError, match="search request"):
        source.collect_nebraska_facility_demand(fetcher, now=NOW)


def test_actual_nyc_meter_maximum_is_not_whole_facility_or_zero_demand():
    data = source.parse_nyc_demand(nyc_rows(), 2024)
    assert len(data["power_sites"]) == len(data["power_demands"]) == 1
    assert len(data["excluded"]) == 65
    row = data["power_demands"][0]
    assert (row["site_id"], row["value"], row["unit"], row["peak_month"], row["publisher_meter_id"]) == ("nyc:21355509", 12.6, "kW", "2024-12", "186709251")
    assert row["measurement_boundary"] == "largest_individual_meter_entry"
    assert row["whole_facility"] is False and row["demand_coverage_verified"] is False
    assert row["interval_minutes"] is None
    zero = [x for x in data["excluded"] if x["raw_reported_demand_kw"] == "0"]
    assert len(zero) == 2 and all("zero facility power" in x["reasons"][0] for x in zero)
    assert len([x for x in data["excluded"] if x["raw_reported_demand_kw"] == "Not Available"]) == 63


@pytest.mark.parametrize("field,value", [("annual_maximum_demand_kw", -1), ("annual_maximum_demand_kw", True),
    ("annual_maximum_demand_mm", "12/2023"), ("annual_maximum_demand_mm", "13/2024"),
    ("annual_maximum_demand_meter", "No meter ID"), ("report_year", "2025"), ("address_1", "")])
def test_nyc_value_period_and_meter_identity_must_be_explicit(field, value):
    row, url, sha = positive_nyc()
    row[field] = value
    with pytest.raises(ValueError): source.parse_nyc_demand([(row, url, sha)], 2024)


@pytest.mark.parametrize("field", list(source.NYC_ENERGY_FLAGS) + ["metered_areas_energy", "construction_status"])
def test_flagged_positive_nyc_value_is_retained_only_as_excluded_evidence(field):
    row, url, sha = positive_nyc()
    row[field] = "Unknown"
    data = source.parse_nyc_demand([(row, url, sha)], 2024)
    assert data["power_demands"] == []
    assert data["excluded"][0]["raw_reported_demand_kw"] == "12.6"


class NycCapture:
    def __init__(self):
        self.saved = json.loads((FIXTURES / "nyc-publisher-capture.json").read_text())

    def get_bytes(self, url):
        return self.saved[url]["raw_text"].encode()


def test_complete_nyc_collector_replays_exact_metadata_count_and_page_captures():
    result = source.collect_nyc_facility_demand(NycCapture(), now=NOW)
    assert result.record_count == 1
    assert result.data["capture"]["selected_property_count"] == 66
    assert result.data["power_demands"][0]["value"] == 12.6


@pytest.mark.parametrize("mutation", ["definition", "month_label", "meter_label"])
def test_nyc_publisher_cannot_silently_change_the_meter_definition_or_auxiliary_fields(mutation):
    fetcher = NycCapture()
    key = next(u for u in fetcher.saved if "/api/views/" in u)
    meta = json.loads(fetcher.saved[key]["raw_text"])
    fields = {c["fieldName"]: c for c in meta["columns"]}
    if mutation == "definition": fields["annual_maximum_demand_kw"]["description"] = "Requested contract connection capacity"
    elif mutation == "month_label": fields["annual_maximum_demand_mm"]["name"] = "Contract start date"
    else: fields["annual_maximum_demand_meter"]["name"] = "Generator ID"
    fetcher.saved[key]["raw_text"] = json.dumps(meta)
    with pytest.raises(ValueError): source.collect_nyc_facility_demand(fetcher, now=NOW)
