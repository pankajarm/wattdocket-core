"""Reviewed campus coverage must survive source drift without guessing joins."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import pytest

from wattdocket.production import identity_meta_expansion as expansion
from wattdocket.production.facility_disclosure_identity import DisclosureIdentityError, resolve_disclosure_links

NOW = datetime(2026, 11, 4, 22, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures/meta_expansion_inventory_2026_11_04.json"


class Captures:
    def __init__(self, monkeypatch):
        self.values, self.receipts = {}, {}
        specs = copy.deepcopy(expansion.EVIDENCE)
        for spec in specs:
            if spec["kind"] == "pdf":
                with pymupdf.open() as doc:
                    for _ in range(spec["page"]):
                        doc.new_page(width=2200, height=842)
                    doc[-1].insert_text((20, 30), "\n".join(spec["excerpts"]), fontsize=10)
                    raw = doc.tobytes()
                spec["expected_sha256"] = hashlib.sha256(raw).hexdigest()
            else:
                raw = ("<html><body>" + "<p>".join(spec["excerpts"]) + "</body></html>").encode()
            self.set(spec["url"], raw)
        monkeypatch.setattr(expansion, "EVIDENCE", specs)

    def set(self, url, raw):
        sha = hashlib.sha256(raw).hexdigest()
        self.values[url] = raw
        self.receipts[url] = {"url": url, "sha256": sha, "raw_key": f"raw/{sha}.gz",
                              "retrieved_at": "2026-11-04T20:00:00Z", "cache_hit": True}

    def get_bytes(self, url):
        value = self.values[url]
        if isinstance(value, Exception):
            raise value
        return value

    def receipt(self, url):
        return self.receipts.get(url)


def records():
    return json.loads(FIXTURE.read_text())["facilities"]


def collect(monkeypatch):
    fetcher = Captures(monkeypatch)
    return expansion.collect_meta_identity(fetcher, now=NOW), fetcher


def test_eight_new_campus_links_validate_without_allocation_or_coordinates(monkeypatch):
    result, _ = collect(monkeypatch)
    assert result.source_id == "meta_disclosure_identity"
    assert result.record_count == 8
    assert result.data["validation"]["primary_documents"] == 10
    assert result.data["validation"]["disclosure_locations"] == 8
    assert result.data["documents_failed"] == 0
    source_records = records()
    original = copy.deepcopy(source_records)
    links = resolve_disclosure_links(source_records, result.data)
    assert source_records == original, "Coverage must not change geometry, identity, or source provenance"
    assert all(row["verification_status"] == "verified" for row in links)
    assert len({row["disclosure_location_id"] for row in links}) == 8
    assert sum(row["record_id"].startswith("osm:") for row in links) == 2
    for row in links:
        assert row["relationship"] == "within_disclosure_location"
        assert row["reporting_scope"] == "campus"
        assert row["allocation_allowed"] is False
        assert row["same_physical_scope_verified"] is False
        assert row["coverage_is_exhaustive"] is False
        assert len(row["evidence"]) == 3
        assert not any(key in row for key in ("observations", "latitude", "longitude", "map_eligible", "grid", "scenario"))


@pytest.mark.parametrize("record_id", ["osm:way:578412815", "osm:way:1035123482"])
@pytest.mark.parametrize("field", ["id", "name", "city", "state", "registry_id", "address_line", "duplicate"])
def test_osm_exact_guards_reject_replaced_or_ambiguous_records(monkeypatch, record_id, field):
    result, _ = collect(monkeypatch)
    rows = records()
    row = next(r for r in rows if r["id"] == record_id)
    if field == "duplicate":
        rows.append(copy.deepcopy(row))
    else:
        row[field] = "different"
    link = next(r for r in resolve_disclosure_links(rows, result.data) if r["record_id"] == record_id)
    assert link["verification_status"] == "candidate"
    assert link["allocation_allowed"] is False


@pytest.mark.parametrize("failure", ["network", "changed_pdf", "receipt", "missing_passage"])
def test_one_changed_document_withholds_only_dependent_link(monkeypatch, failure):
    fetcher = Captures(monkeypatch)
    key = "meta-expand-los-lunas" if failure == "changed_pdf" else "meta-expand-eagle-mountain"
    spec = next(s for s in expansion.EVIDENCE if s["id"] == key)
    if failure == "network":
        fetcher.values[spec["url"]] = RuntimeError("HTTP 503")
    elif failure == "changed_pdf":
        fetcher.set(spec["url"], fetcher.values[spec["url"]] + b"changed")
    elif failure == "receipt":
        fetcher.receipts[spec["url"]]["raw_key"] = "unrelated.gz"
    else:
        fetcher.set(spec["url"], b"<html>Only a mailing address is available</html>")
    result = expansion.collect_meta_identity(fetcher, now=NOW)
    links = resolve_disclosure_links(records(), result.data)
    assert result.record_count == 7
    assert result.data["documents_failed"] == 1
    assert result.data["failed_documents"][0]["id"] == key
    assert sum(r["verification_status"] == "verified" for r in links) == 7
    candidate = next(r for r in links if r["verification_status"] == "candidate")
    assert key in candidate["evidence_ids"]
    assert "Reviewed primary evidence unavailable" in candidate["reason"]


def test_absent_parent_operator_proof_cannot_create_city_name_links(monkeypatch):
    fetcher = Captures(monkeypatch)
    spec = next(s for s in expansion.EVIDENCE if s["id"] == "meta-expand-subsidiaries")
    fetcher.set(spec["url"], b"<html>Names without verified parent ownership</html>")
    with pytest.raises(DisclosureIdentityError, match="No reviewed link"):
        expansion.collect_meta_identity(fetcher, now=NOW)


def test_no_links_to_unverified_campuses_or_duplicate_registrations(monkeypatch):
    result, _ = collect(monkeypatch)
    linked = {x["disclosure_location_id"] for x in result.data["links"]}
    assert not linked & {"meta:mesa-az", "meta:dekalb-il", "meta:huntsville-al", "meta:gallatin-tn"}
    rows = records()
    # A second Siculus registry, city/name match, or Apple/Platypus record is
    # insufficient. Only the explicit reviewed ID and physical street qualify.
    for name, fid in [("SICULUS, INC", "echo:110046462421"), ("PLATYPUS DEVELOPMENT LLC", "echo:999999999999")]:
        clone = copy.deepcopy(rows[0])
        clone.update(id=fid, registry_id=fid.split(":")[1], name=name)
        rows.append(clone)
    links = resolve_disclosure_links(rows, result.data)
    assert len(links) == 8
    assert all(x["record_id"] not in {"echo:110046462421", "echo:999999999999"} for x in links)


def test_retrieval_is_not_relabelled_as_review_or_document_date(monkeypatch):
    result, _ = collect(monkeypatch)
    for item in result.data["evidence"]:
        assert item["retrieved_at"] == "2026-11-04T20:00:00Z"
        assert item["checked_at"] == "2026-11-04T22:00:00Z"
        assert item["published_at"] is None
    items = {x["id"]: x for x in result.data["evidence"]}
    assert items["meta-expand-los-lunas"]["document_date"] == "2024-03-11"
    assert items["meta-expand-subsidiaries"]["document_period_end"] == "2024-12-31"


def test_new_albany_range_and_osm_extent_limitations_remain_explicit():
    indexed = {r["disclosure_location_id"]: r for r in expansion.REVIEWED_LINKS}
    assert "1300–1700" in indexed["meta:new-albany-oh"]["note"]
    assert "not independently verified" in indexed["meta:eagle-mountain-ut"]["note"]
    for key in ("meta:eagle-mountain-ut", "meta:los-lunas-nm"):
        assert indexed[key]["expected_record"]["registry_id"] is None
