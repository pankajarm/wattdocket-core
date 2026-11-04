"""Disclosure coverage must not become a building total or fuzzy identity join."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

from wattdocket.production import facility_disclosure_identity as identity

NOW = datetime(2026, 11, 3, 23, 45, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures/disclosure_identity_inventory_2026_11_03.json"


class Captures:
    def __init__(self, monkeypatch):
        self.values, self.receipts = {}, {}
        specs = copy.deepcopy(identity.EVIDENCE)
        for spec in specs:
            if spec["kind"] == "pdf":
                document = pymupdf.open()
                spec["excerpts"] = [value.replace("“", '"').replace("”", '"') for value in spec["excerpts"]]
                # PDF extraction and page boundaries are tested without
                # committing a county's 11 MB annual financial report.
                for _ in range(spec["page"]):
                    document.new_page(width=1500, height=842)
                document[-1].insert_text((20, 30), "\n".join(spec["excerpts"]), fontsize=10)
                raw = document.tobytes()
                document.close()
                spec["expected_sha256"] = hashlib.sha256(raw).hexdigest()
            elif spec["kind"] == "json":
                raw = json.dumps([spec["row_selector"]]).encode()
            elif spec["kind"] == "arcgis":
                raw = json.dumps({"features": [{"attributes": spec["row_selector"]}]}).encode()
            else:
                raw = ("<html><meta content='" + str(spec.get("published_at")) + "'><body>" +
                       "<p>".join(spec["excerpts"]) + "</body></html>").encode()
            self.set(spec["url"], raw)
        monkeypatch.setattr(identity, "EVIDENCE", specs)

    def set(self, url, raw):
        sha = hashlib.sha256(raw).hexdigest()
        self.values[url] = raw
        self.receipts[url] = {"url": url, "sha256": sha, "raw_key": f"raw/{sha}.gz",
                              "retrieved_at": "2026-11-01T12:00:00Z", "cache_hit": True}

    def get_bytes(self, url):
        return self.values[url]

    def receipt(self, url):
        return self.receipts.get(url)


def collect(monkeypatch):
    fetcher = Captures(monkeypatch)
    return identity.collect_disclosure_identity(fetcher, now=NOW), fetcher


def facilities():
    return json.loads(FIXTURE.read_text())["facilities"]


def test_primary_captures_and_inventory_guards_produce_coverage_links_only(monkeypatch):
    result, _ = collect(monkeypatch)
    assert result.record_count == 6
    assert result.data["validation"]["primary_documents"] == 10
    assert result.data["validation"]["disclosure_locations"] == 5
    assert all(item["verification_status"] == "pending_inventory_check" for item in result.data["links"])
    links = identity.resolve_disclosure_links(facilities(), result.data)
    assert len(links) == 6
    assert all(item["verification_status"] == "verified" for item in links)
    assert all(item["relationship"] == "within_disclosure_location" for item in links)
    assert all(item["allocation_allowed"] is False and item["same_physical_scope_verified"] is False for item in links)
    assert all(item["coverage_is_exhaustive"] is False for item in links)
    # Two registrations are not two separately measured Google campuses.
    assert sum(x["disclosure_location_id"] == "google:douglas-county-ga" for x in links) == 2
    assert all(len(item["evidence"]) == 2 and item["expected_record"] for item in links)
    assert not any(x["record_id"] == "echo:110071294203" for x in links), "Other Scout record must not inherit Henrico"


def test_cached_retrieval_and_document_periods_are_not_changed_to_check_time(monkeypatch):
    result, _ = collect(monkeypatch)
    evidence = {x["id"]: x for x in result.data["evidence"]}
    assert evidence["henrico-acfr"]["page"] == 152
    assert evidence["henrico-acfr"]["published_at"] is None
    assert evidence["henrico-acfr"]["document_period_end"] == "2025-06-30"
    assert evidence["henrico-acfr"]["retrieved_at"] == "2026-11-01T12:00:00Z"
    assert evidence["henrico-acfr"]["checked_at"] == "2026-11-03T23:45:00Z"
    assert evidence["meta-henrico"]["published_at"] == "2020-08-05"


@pytest.mark.parametrize("mutation", ["address", "city", "name", "registry_id", "program", "duplicate", "missing"])
def test_current_inventory_identity_changes_fail_closed(monkeypatch, mutation):
    result, _ = collect(monkeypatch)
    records = facilities()
    record = next(x for x in records if x["id"] == "echo:110061650100")
    if mutation == "program":
        record["record_summary"] = []
    elif mutation == "duplicate":
        records.append(copy.deepcopy(record))
    elif mutation == "missing":
        records.remove(record)
    else:
        record[mutation] = "changed"
    link = next(x for x in identity.resolve_disclosure_links(records, result.data) if x["record_id"] == "echo:110061650100")
    assert link["verification_status"] == "candidate"
    assert link["relationship"] == "candidate"
    assert link["allocation_allowed"] is False


def test_arbitrary_sibling_or_matching_city_does_not_add_a_link(monkeypatch):
    result, _ = collect(monkeypatch)
    records = facilities()
    sibling = copy.deepcopy(records[0])
    sibling.update(id="echo:199999999999", registry_id="199999999999")
    records.append(sibling)
    links = identity.resolve_disclosure_links(records, result.data)
    assert len(links) == 6
    assert not any(x["record_id"] == sibling["id"] for x in links)


@pytest.mark.parametrize("problem", ["passage", "script_only", "receipt", "pdf_hash", "publication_date", "row_duplicate", "mailing_address"])
def test_changed_or_uncaptured_evidence_requires_review(monkeypatch, problem):
    fetcher = Captures(monkeypatch)
    specs = {x["id"]: x for x in identity.EVIDENCE}
    if problem in ("passage", "script_only", "publication_date"):
        spec = specs["meta-henrico"]
        raw = fetcher.values[spec["url"]]
        if problem == "passage":
            raw = raw.replace(b"Henrico Data Center", b"another campus")
        elif problem == "publication_date":
            raw = raw.replace(b"2020-08-05", b"2021-08-05")
        else:
            raw = ("<meta content='2020-08-05'><script>" + " ".join(spec["excerpts"]) + "</script>").encode()
        fetcher.set(spec["url"], raw)
    elif problem == "receipt":
        fetcher.receipts[specs["google-georgia"]["url"]]["sha256"] = "0" * 64
    elif problem == "pdf_hash":
        url = specs["henrico-acfr"]["url"]
        fetcher.set(url, fetcher.values[url] + b"changed")
    else:
        spec = specs["oregon-vitesse"]
        row = copy.deepcopy(spec["row_selector"])
        if problem == "row_duplicate":
            rows = [row, row]
        else:
            row["company_mailing_address"] = row.pop("facility_location_address")
            rows = [row]
        fetcher.set(spec["url"], json.dumps(rows).encode())
    with pytest.raises(identity.DisclosureIdentityError):
        identity.collect_disclosure_identity(fetcher, now=NOW)


def test_missing_evidence_or_same_scope_promotion_is_not_verified(monkeypatch):
    result, _ = collect(monkeypatch)
    result.data["evidence"][0]["verified"] = False
    result.data["links"][-1]["relationship"] = "same_physical_scope"
    links = identity.resolve_disclosure_links(facilities(), result.data)
    assert all(x["verification_status"] == "candidate" for x in (links[0], links[1], links[-1]))
    assert all(x["allocation_allowed"] is False for x in links)
