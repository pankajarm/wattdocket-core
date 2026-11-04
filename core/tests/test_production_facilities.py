from datetime import datetime, timezone
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from wattdocket.production.sources_facilities import (
    READER, VA_REGISTRY, SourceError, collect_texas, collect_virginia,
    extract_virginia_document, fetch_echo_rows, normalize_echo, normalize_osm,
    parse_virginia_registry, _reader_pages,
)

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)
DOCUMENT_URL = "https://www.deq.virginia.gov/home/showpublisheddocument/123/456"
REGISTRY = f"""Title: Virginia DEQ
## Issued Air Permits for Data Centers as of Nov. 2, 2026
| Site | Permit | Issued | Program | County | Office |
| --- | --- | --- | --- | --- | --- |
| Site One | [**74063-**]({DOCUMENT_URL})**[5]({DOCUMENT_URL})** | 03/26-2026 | Article 6 | Loudoun Co. | Northern |
| Site Two | **[74288-2]({DOCUMENT_URL})** | 06/01/2026 | Article 6 | Loudoun Co. | Northern |
"""


def test_registry_covers_split_links_bold_variants_and_publisher_date_typo():
    rows, date = parse_virginia_registry(REGISTRY)
    assert [r["registration"] for r in rows] == ["74063-5", "74288-2"]
    assert rows[0]["issued"] == "03/26-2026"
    assert rows[0]["issued_iso"] == "2026-03-26"
    assert date == "2026-11-02"


def test_registry_html_and_markdown_same_records():
    raw = f"""<h2>Issued Air Permits for Data Centers as of November 2, 2026</h2>
    <table><tr><td>Site One</td><td><a href='{DOCUMENT_URL}'>74063-</a><b>5</b></td>
    <td>03/26-2026</td><td>Article 6</td><td>Loudoun Co.</td><td>Northern</td></tr></table>"""
    rows, stamp = parse_virginia_registry(raw)
    assert rows[0] == parse_virginia_registry(REGISTRY)[0][0]
    assert stamp == "2026-11-02"


def test_registry_rejects_unparsed_rows_instead_of_shrinking_dataset():
    with pytest.raises(SourceError, match="registration"):
        parse_virginia_registry(REGISTRY.replace("74288-2", "unknown"))
    with pytest.raises(SourceError, match="repeats"):
        parse_virginia_registry(REGISTRY + REGISTRY.splitlines()[-1])


def test_registry_rejects_offsite_document_links():
    with pytest.raises(SourceError, match="unexpected document host"):
        parse_virginia_registry(REGISTRY.replace("www.deq.virginia.gov", "example.net"))


class EchoPages:
    def __init__(self, *, repeated=False, changed=False, missing=False):
        self.repeated, self.changed, self.missing = repeated, changed, missing
        self.requests = []

    def get_json(self, url):
        self.requests.append(url)
        args = parse_qs(urlparse(url).query)
        if ".metadata" in url:
            return {"Results": {"ResultColumns": [
                {"ObjectName": "RegistryID", "ColumnID": 6},
                {"ObjectName": "FacLat", "ColumnID": 19},
                {"ObjectName": "FacLong", "ColumnID": 20}]}}
        if ".get_facilities" in url:
            return {"Results": {"QueryRows": 4, "QueryID": "fixed"}}
        page = int(args["pageno"][0])
        assert args["qcolumns"] == ["6,19,20"]
        ids = [1, 2] if self.repeated or page == 1 else [3, 4]
        if self.missing and page == 2:
            ids.pop()
        return {"Results": {"QueryRows": 5 if self.changed else 4,
                            "QueryID": "fixed", "PageNo": page,
                            "Facilities": [{"RegistryID": n} for n in ids]}}


def test_echo_explicit_columns_and_all_pages():
    fetcher = EchoPages()
    rows = fetch_echo_rows(fetcher, "echo", page_size=2)
    assert [x["RegistryID"] for x in rows] == [1, 2, 3, 4]


@pytest.mark.parametrize("option", ["repeated", "changed", "missing"])
def test_echo_incomplete_or_unstable_query_is_not_a_success(option):
    with pytest.raises(SourceError):
        fetch_echo_rows(EchoPages(**{option: True}), "echo", page_size=2)


def test_echo_identity_uses_ids_preserves_missing_coords_and_permit_rows():
    combined = [{"RegistryID": "110000000001", "FacName": "Same name", "FacLat": "38", "FacLong": "-77"},
                {"RegistryID": "110000000002", "FacName": "Same name"}]
    air = [{"RegistryID": "110000000001", "SourceID": "VA1", "AIRStatus": "Permanently Closed"},
           {"RegistryID": "110000000001", "SourceID": "VA2", "AIRStatus": "Permanently Closed"}]
    rows = normalize_echo(combined, air)
    assert len(rows) == 2
    assert rows[0]["status"] == "closed"
    assert len(rows[0]["air_records"]) == 2
    assert rows[1]["lat"] is None and rows[1]["lon"] is None
    assert all(not x["class_verified"] for x in rows)


def test_echo_coordinate_disagreement_is_visible_and_frs_coordinate_retained():
    rows = normalize_echo(
        [{"RegistryID": "110000000001", "FacLat": 38, "FacLong": -77}],
        [{"RegistryID": "110000000001", "SourceID": "VA1", "FacLat": 39, "FacLong": -78}])
    assert rows[0]["lat"] == 38
    assert rows[0]["coordinate_conflicts"] == [{"source_id": "VA1", "lat": 39, "lon": -78}]


def test_echo_orphan_program_id_is_retained_but_not_mislabeled_frs():
    rows = normalize_echo([{"RegistryID": "TXR1551ZU", "FacName": "AWS ALE 64", "NPDESIDs": "TXR1551ZU"}], [])
    assert rows[0]["id"] == "echo:TXR1551ZU"
    assert rows[0]["registry_id"] is None
    assert rows[0]["identity_kind"] == "program_record"
    assert rows[0]["program_ids"]["cwa"] == "TXR1551ZU"


def test_echo_coordinate_accuracy_belongs_to_selected_coordinate_source():
    rows = normalize_echo(
        [{"RegistryID": "110000000001", "FacLat": 38, "FacLong": -77, "FacAccuracyMeters": "17483"},
         {"RegistryID": "110000000002"}],
        [{"RegistryID": "110000000001", "SourceID": "TX1", "FacLat": 38, "FacLong": -77, "CalculatedAccuracyMeters": "50"},
         {"RegistryID": "110000000002", "SourceID": "TX2", "FacLat": 39, "FacLong": -78, "CalculatedAccuracyMeters": "100"}])
    assert rows[0]["coordinate_accuracy_m"] == 17483
    assert rows[0]["coordinate_quality"]["accuracy_field"] == "FacAccuracyMeters"
    assert rows[1]["coordinate_source"] == "icis_air"
    assert rows[1]["coordinate_accuracy_m"] == 100


def permit_text():
    return ("Title: Air Permit\nNumber of Pages: 9\nMarkdown Content:\n"
            "Preceding page content Registration Number: 74063 Page 7\n"
            "Virginia DEQ authorizes the following emergency diesel generators. "
            "The emergency generators shall not operate more than 100 hours per year. "
            "Registration Number: 74063 January 1, 2026 Page 8\n"
            "The generators shall consume no more than 235,200 gallons of diesel. "
            "Registration Number: 74063 January 1, 2026 Page 9\n").encode()


def test_reader_assigns_body_to_preceding_printed_header_not_next_page():
    raw = permit_text()
    result = extract_virginia_document(raw, DOCUMENT_URL, retrieved_via=READER, registration="74063-5")
    hours = next(o for o in result["obligations"] if o["metric"] == "generator_hours_limit")
    assert hours["value"] == 100
    assert hours["citation"]["page"] == 7
    assert hours["citation"]["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert "document_sha256" not in hours["citation"]
    assert result["representation"] == "reader_text"


def test_unpaginated_text_cannot_create_fabricated_page_citations():
    with pytest.raises(SourceError, match="paginated"):
        extract_virginia_document(permit_text().replace(b"Page ", b"Section "),
                                  DOCUMENT_URL, retrieved_via=READER, registration="74063-5")


def test_equipment_aggregate_has_a_receipt_for_every_row_and_scope():
    raw = ("Markdown Content:\nVirginia DEQ issued air permit, emergency backup equipment table. "
           "Registration Number 74063 Page 3\n"
           "(12) Caterpillar emergency gen-sets 3,633 bhp 2,500 ekW; "
           "(2) Caterpillar emergency gen-sets 3,633 bhp 1,000 ekW; "
           "Registration Number 74063 Page 4\n").encode()
    result = extract_virginia_document(raw, DOCUMENT_URL, retrieved_via=READER, registration="74063-5")
    table = result["equipment"][0]
    assert table["value"] == 14 and table["mw_total"] == 32
    assert len(table["citations"]) == table["rows"] == 2
    assert table["scope"] == "equipment_table_page" and table["is_complete_fleet"] is False


def test_reader_cover_letter_page_reset_keeps_only_unambiguous_numbered_pages():
    raw = ("Markdown Content:\nCover letter Page 2\nPermit cover Page 2\n"
           "The emergency diesel generators shall not operate more than 100 hours per year. "
           "This restriction applies to each emergency diesel unit listed in the issued permit. "
           "Registration Number 74063 Page 3\n").encode()
    result = extract_virginia_document(raw, DOCUMENT_URL, retrieved_via=READER, registration="74063-5")
    assert result["page_numbers"] == [2]
    assert result["obligations"][0]["citation"]["page"] == 2


def test_real_virginia_pdf_header_order_matches_official_page_two():
    # Independently checked in the publisher's PDF, 74190-2 physical page 2.
    # The legacy extractor incorrectly treated its top header as a footer.
    path = Path(__file__).resolve().parents[2] / "data/va_permits/74190_2.txt"
    pages = _reader_pages(path.read_text())
    assert "INTRODUCTION" in pages[2]
    assert "EG-POD 11" in pages[2] and "EG-POD 13" in pages[2]
    assert "EG-POD 14" in pages[3]


def test_distinct_engine_group_limits_are_preserved_instead_of_picking_first():
    raw = ("Markdown Content:\nCover Page 2\n"
           "The Group A emergency generators shall not operate more than 100 hours per year. "
           "The Group B emergency generators shall not operate more than 500 hours per year. "
           "Registration Number 74063 Page 3\n").encode()
    result = extract_virginia_document(raw, DOCUMENT_URL, retrieved_via=READER, registration="74063-5")
    hours = [o for o in result["obligations"] if o["metric"] == "generator_hours_limit"]
    assert [o["value"] for o in hours] == [100, 500]
    assert all(not o["applies_to_entire_site"] for o in hours)


def test_failed_document_does_not_remove_registry_entry_or_invent_facts():
    class Fetcher:
        def get_bytes(self, url):
            if url == READER + VA_REGISTRY:
                return REGISTRY.encode()
            raise OSError("HTTP 403")
    result = collect_virginia(Fetcher(), now=NOW)
    assert result.record_count == 2
    assert result.data["documents_failed"] == 2
    assert all(p["document"]["obligations"] == [] for p in result.data["permits"])
    assert result.data["registry_retrieved_via"] == READER


def test_registry_reuses_exact_archived_document_without_inventing_retrieval_date(tmp_path):
    from wattdocket.production.storage import LocalStore, json_bytes
    store = LocalStore(tmp_path)
    old = {"registration": "74063-5", "permit_url": DOCUMENT_URL,
           "document": {"status": "parsed", "extraction_version": "va-deterministic-3", "retrieved_at": None,
                        "archived_by_at": "2026-10-28T06:05:40Z", "obligations": [{"value": 100}]}}
    store.put("state/sources/virginia_permits.json", json_bytes({"result": {"data": {"permits": [old]}}}))
    class Fetcher:
        def __init__(self):
            self.store, self.requests = store, []
        def get_bytes(self, url):
            self.requests.append(url)
            if url == VA_REGISTRY:
                return REGISTRY.encode()
            raise OSError("HTTP 403")
    fetcher = Fetcher()
    result = collect_virginia(fetcher, now=NOW)
    doc = result.data["permits"][0]["document"]
    assert doc["cache_reused"] and doc["freshness"] == "archived"
    assert doc["retrieved_at"] is None and doc["archived_by_at"] == old["document"]["archived_by_at"]
    assert doc["latest_fetch_status"] == "not_attempted_cached"
    assert result.data["documents_cached"] == 1
    # Second row shares URL but differs in registration: it MUST be attempted.
    assert fetcher.requests.count(DOCUMENT_URL) == 1
    assert result.data["permits"][1]["document"]["status"] == "failed"


def test_osm_preserves_independent_buildings_and_crowdsource_license():
    payload = {"osm3s": {"timestamp_osm_base": "2026-11-03T00:00:00Z"}, "elements": [
        {"type": "way", "id": 1, "center": {"lat": 38, "lon": -77},
         "tags": {"name": "Campus", "telecom": "data_center"}},
        {"type": "node", "id": 2, "lat": 38, "lon": -77,
         "tags": {"name": "Campus", "proposed:telecom": "data_center"}}]}
    rows = normalize_osm(payload)
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"]
    assert rows[1]["status"] == "proposed"
    assert all(r["license"] == "ODbL-1.0" and not r["class_verified"] for r in rows)
    payload["remark"] = "runtime error: Query timed out"
    with pytest.raises(SourceError, match="partial/error"):
        normalize_osm(payload)


def test_texas_does_not_turn_a_downloaded_draft_into_an_effective_permit():
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 60), "Statement of Basis of the Federal Operating Permit\n"
                         "Draft permit conditions for an example data center.\n"
                         "Prepared on: February 27, 2026\n"
                         "This is a document statement, not a measured operating record.")
        raw = pdf.tobytes()
    class Fetcher:
        def get_bytes(self, url):
            return raw
        def receipt(self, url):
            return {"retrieved_at": "2026-10-20T00:00:00Z", "cache_hit": True}
    result = collect_texas(Fetcher(), now=NOW)
    assert result.data["documents_ok"] == 4
    assert result.data["latest_effective_status_verified"] is False
    for document in result.data["documents"]:
        assert document["is_current_effective_limit"] is False
        assert document["content_sha256"] == hashlib.sha256(raw).hexdigest()
        assert document["receipt"]["retrieved_at"] == "2026-10-20T00:00:00Z"
    assert result.observation_end == "2026-02-27"


def test_texas_fails_source_if_all_document_responses_are_errors():
    class Fetcher:
        def get_bytes(self, url):
            return b"<html>Access denied</html>"
    with pytest.raises(SourceError, match="All selected Texas"):
        collect_texas(Fetcher(), now=NOW)
