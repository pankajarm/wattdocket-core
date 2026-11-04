"""Captured government rows must verify only their reviewed address relationship."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wattdocket.production import identity_municipal_expansion as expansion
from wattdocket.production.facility_disclosure_identity import DisclosureIdentityError, resolve_disclosure_links

FIXTURES = Path(__file__).parent / "fixtures/municipal_reporting"
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


class Captures:
    def __init__(self):
        saved = json.loads((FIXTURES / "identity_evidence.json").read_text())
        self.values = {url: item["raw_text"].encode() for url, item in saved.items()}
        self.receipts = {url: item["receipt"] for url, item in saved.items()}

    def get_bytes(self, url):
        value = self.values[url]
        if isinstance(value, Exception):
            raise value
        return value

    def receipt(self, url):
        return self.receipts[url]

    def mutate(self, url, update):
        payload = json.loads(self.values[url])
        update(payload)
        raw = json.dumps(payload).encode()
        sha = hashlib.sha256(raw).hexdigest()
        self.values[url] = raw
        self.receipts[url].update(sha256=sha, raw_key=f"raw/{sha}.gz")


def records():
    return json.loads((FIXTURES / "inventory.json").read_text())["facilities"]


def test_actual_captured_government_rows_resolve_three_nonallocating_building_links():
    fetcher = Captures()
    # These are the exact original HTTP bytes, not a synthetic reconstruction.
    for url, raw in fetcher.values.items():
        assert hashlib.sha256(raw).hexdigest() == fetcher.receipt(url)["sha256"]
    result = expansion.collect_municipal_identity(fetcher, now=NOW)
    original = records()
    source_records = copy.deepcopy(original)
    links = resolve_disclosure_links(source_records, result.data)
    assert source_records == original
    assert result.source_id == "municipal_disclosure_identity"
    assert result.record_count == 3 and result.data["documents_failed"] == 0
    assert result.data["validation"]["primary_documents"] == 6
    assert len(links) == 3
    for link in links:
        assert link["verification_status"] == "verified"
        assert link["relationship"] == "within_disclosure_location"
        assert link["reporting_scope"] == "building"
        assert link["allocation_allowed"] is False
        assert link["same_physical_scope_verified"] is False
        assert link["coverage_is_exhaustive"] is False
        assert len(link["evidence"]) == 2
        assert "whole-building meter/reporting boundary is not established as equal" in link["note"]
        assert not any(k in link for k in ("observations", "latitude", "longitude", "grid", "scenario"))


@pytest.mark.parametrize("field", ["id", "registry_id", "name", "address_line", "city", "state", "program_ids", "duplicate"])
def test_exact_inventory_guards_reject_address_program_or_entity_drift(field):
    result = expansion.collect_municipal_identity(Captures(), now=NOW)
    rows = records()
    row = next(r for r in rows if r["id"] == "echo:110024314222")
    if field == "duplicate": rows.append(copy.deepcopy(row))
    else: row[field] = {} if field == "program_ids" else "different"
    links = resolve_disclosure_links(rows, result.data)
    affected = next(link for link in links if link["record_id"] == "echo:110024314222")
    assert affected["verification_status"] == "candidate"
    assert affected["allocation_allowed"] is False
    assert sum(link["verification_status"] == "verified" for link in links) == 2


@pytest.mark.parametrize("failure", ["network", "address", "registry", "duplicate_row", "receipt"])
def test_changed_epa_document_withholds_only_its_link(failure):
    fetcher = Captures()
    spec = next(s for s in expansion.EVIDENCE if s["id"] == "municipal-frs-internap-somerville")
    url = spec["url"]
    if failure == "network": fetcher.values[url] = RuntimeError("HTTP 503")
    elif failure == "receipt": fetcher.receipts[url]["raw_key"] = "raw/unrelated.gz"
    elif failure == "duplicate_row": fetcher.mutate(url, lambda rows: rows.append(copy.deepcopy(rows[0])))
    else:
        field = "location_address" if failure == "address" else "registry_id"
        fetcher.mutate(url, lambda rows: rows[0].update({field: "different"}))
    result = expansion.collect_municipal_identity(fetcher, now=NOW)
    links = resolve_disclosure_links(records(), result.data)
    assert result.record_count == 2 and result.data["documents_failed"] == 1
    assert result.data["failed_documents"][0]["id"] == spec["id"]
    assert sum(link["verification_status"] == "verified" for link in links) == 2
    assert next(x for x in links if x["record_id"] == "echo:110024314222")["verification_status"] == "candidate"


@pytest.mark.parametrize("field", ["UBID", "Street_Address", "City", "Gross_Floor_Area"])
def test_changed_building_boundary_or_city_requires_new_review(field):
    fetcher = Captures()
    spec = next(s for s in expansion.EVIDENCE if s["id"] == "municipal-ma-ssb-westborough")
    fetcher.mutate(spec["url"], lambda payload: payload["features"][0]["attributes"].update({field: "changed"}))
    result = expansion.collect_municipal_identity(fetcher, now=NOW)
    assert result.record_count == 2
    affected = next(x for x in resolve_disclosure_links(records(), result.data) if x["record_id"] == "echo:110070550595")
    assert affected["verification_status"] == "candidate"


def test_no_complete_evidence_cannot_report_a_successful_identity_partition():
    fetcher = Captures()
    for spec in expansion.EVIDENCE:
        if spec["kind"] == "arcgis": fetcher.values[spec["url"]] = RuntimeError("HTTP 503")
    with pytest.raises(DisclosureIdentityError, match="No reviewed link"):
        expansion.collect_municipal_identity(fetcher, now=NOW)


def test_original_retrieval_dates_and_known_historical_identity_limitations_survive():
    fetcher = Captures()
    result = expansion.collect_municipal_identity(fetcher, now=NOW)
    for item in result.data["evidence"]:
        assert item["retrieved_at"] == fetcher.receipt(item["url"])["retrieved_at"]
        assert item["retrieved_at"].startswith("2026-11-04")
        assert item["checked_at"] == "2026-11-05T00:00:00Z"
        assert item["published_at"] is None
    links = {x["record_id"]: x for x in result.data["links"]}
    assert "Permanently Closed" in links["echo:110024314222"]["note"]
    assert "NAME MISMATCH" in links["echo:110009589267"]["note"]
    # One FRS record spans 115 and 125 Second Avenue; the single-building
    # disclosure is insufficient to establish that whole-record coverage.
    assert "echo:110041000314" not in links

