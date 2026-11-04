"""Release safety tests: failures must not turn into new public facts."""
from datetime import datetime, timedelta, timezone
from email.message import Message
import gzip
import json
import urllib.error

import pytest

from wattdocket.production import pipeline
from wattdocket.production.build import Geometry, _permit_matches, build_release
from wattdocket.production.fetch import CaptureFetcher, stamp
from wattdocket.production.models import SourceResult
from wattdocket.production.storage import ConflictError, LocalStore, S3Store, digest, json_bytes, read_json
from wattdocket.production.texas_evidence import for_facility

NOW = datetime(2026, 11, 3, 20, tzinfo=timezone.utc)


def result(sid="echo", *, count=1000, data=None, observed=None):
    return SourceResult(sid, sid, "https://publisher.example/" + sid, data or {"marker": "new"}, count,
                        observation_end=observed)


def state(source, *, retrieved=NOW):
    record = source.as_dict()
    return {"id": source.source_id, "label": source.label, "url": source.url, "result": record,
            "result_sha256": digest(json_bytes(record)), "record_count": source.record_count,
            "last_attempt_at": stamp(retrieved), "last_success_at": stamp(retrieved),
            "retrieved_at": stamp(retrieved), "observation_end": source.observation_end,
            "status": "current", "error": None}


class EmptyFetcher:
    def __init__(self, *args):
        self.receipts = {}


class RecordingStore(LocalStore):
    def __init__(self, path, fail_at=None):
        super().__init__(path)
        self.writes = []
        self.fail_at = fail_at

    def put(self, key, data, **kwargs):
        self.writes.append(key)
        if key == self.fail_at:
            raise OSError("injected storage failure")
        return super().put(key, data, **kwargs)


def test_last_good_survives_network_error_with_original_retrieval_date(tmp_path):
    store = LocalStore(tmp_path)
    prior = state(result(data={"marker": "last good"}), retrieved=NOW - timedelta(days=1))
    store.put("state/sources/echo.json", json_bytes(prior))

    def fail(fetcher, **kwargs):
        fetcher.receipts["https://publisher.example/error"] = {"sha256": "error receipt"}
        raise TimeoutError("upstream unavailable")

    actual = pipeline.collect_one("echo", fail, store, NOW, force=True, fetcher_factory=EmptyFetcher)
    assert actual["result"] == prior["result"]
    assert actual["result_sha256"] == prior["result_sha256"]
    assert actual["retrieved_at"] == prior["retrieved_at"]
    assert actual["last_success_at"] == prior["last_success_at"]
    assert actual["status"] == "stale" and "upstream unavailable" in actual["error"]
    assert actual["failed_attempt_receipts"] == [{"sha256": "error receipt"}]


@pytest.mark.parametrize("bad", [result(count=899), result(sid="osm"), result(data={"bad": float("nan")})])
def test_shrunken_misidentified_or_nonfinite_source_cannot_replace_last_good(tmp_path, bad):
    store = LocalStore(tmp_path)
    prior = state(result(), retrieved=NOW - timedelta(days=1))
    store.put("state/sources/echo.json", json_bytes(prior))
    actual = pipeline.collect_one("echo", lambda *a, **k: bad, store, NOW, force=True, fetcher_factory=EmptyFetcher)
    assert actual["result"] == prior["result"]
    assert actual["error"] and actual["status"] == "stale"


def test_recent_success_not_refetched_and_failed_source_retries_after_hour(tmp_path):
    store = LocalStore(tmp_path)
    calls = []
    adapter = lambda *a, **k: calls.append(True) or result()
    prior = state(result(), retrieved=NOW - timedelta(hours=2))
    store.put("state/sources/echo.json", json_bytes(prior))
    pipeline.collect_one("echo", adapter, store, NOW, fetcher_factory=EmptyFetcher)
    assert calls == []
    prior["error"] = "previous attempt failed"
    store.put("state/sources/echo.json", json_bytes(prior))
    actual = pipeline.collect_one("echo", adapter, store, NOW, fetcher_factory=EmptyFetcher)
    assert calls == [True] and actual["status"] == "current"


def test_partial_permit_corpus_cannot_claim_full_success(tmp_path):
    store = LocalStore(tmp_path)
    actual = pipeline.collect_one("virginia_permits", lambda *a, **k: result("virginia_permits", count=194,
                                  data={"documents_failed": 7}), store, NOW, fetcher_factory=EmptyFetcher)
    assert actual["partial"] is True and actual["documents_failed"] == 7
    assert "7 linked documents failed" in actual["note"]


def test_permit_retry_keeps_exact_document_last_good_and_dates_without_hiding_failure(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    monkeypatch.setitem(pipeline.POLICIES, "virginia_permits", {"minimum": 1, "interval": 1, "stale": 86400})
    old_doc = {"status": "parsed", "extraction_version": "va-deterministic-3", "retrieved_at": None,
               "archived_by_at": "2026-10-28T06:05:40Z", "obligations": [{"value": 100}],
               "content_sha256": "a" * 64, "receipt": {"sha256": "a" * 64}}
    permit = {"registration": "74269-2", "permit_url": "https://publisher.example/version1", "document": old_doc}
    prior = state(result("virginia_permits", count=1, data={"permits": [permit]}))
    store.put("state/sources/virginia_permits.json", json_bytes(prior))
    new_permit = {**permit, "document": {"status": "failed", "error": "HTTP 429", "obligations": []}}
    actual = pipeline.collect_one("virginia_permits", lambda *a, **k: result("virginia_permits", count=1,
                                  data={"permits": [new_permit]}), store, NOW, force=True, fetcher_factory=EmptyFetcher)
    data = actual["result"]["data"]
    doc = data["permits"][0]["document"]
    assert doc["freshness"] == "stale" and doc["latest_fetch_error"] == "HTTP 429"
    assert doc["retrieved_at"] is None and doc["archived_by_at"] == old_doc["archived_by_at"]
    assert doc["obligations"] == old_doc["obligations"] and doc["receipt"] == old_doc["receipt"]
    assert data["documents_failed"] == data["documents_stale"] == data["documents_ok"] == 1
    assert data["documents_current_parsed"] == data["documents_unavailable"] == 0
    assert actual["partial"] is True


@pytest.mark.parametrize("change", ["registration", "permit_url", "extraction_version"])
def test_permit_retry_never_inherits_different_document_or_obsolete_page_parser(change):
    old_doc = {"status": "parsed", "extraction_version": "va-deterministic-3", "obligations": [{"value": 100}]}
    old = {"registration": "74269-2", "permit_url": "https://publisher.example/v1", "document": old_doc}
    new = {**old, "document": {"status": "failed", "error": "HTTP 429", "obligations": []}}
    if change == "extraction_version":
        old_doc[change] = "va-deterministic-2"
    else:
        new[change] += "changed"
    value = result("virginia_permits", data={"permits": [new]}).as_dict()
    pipeline.retain_virginia_document_evidence(value, state(result("virginia_permits", data={"permits": [old]})), stamp(NOW))
    doc = value["data"]["permits"][0]["document"]
    assert doc["freshness"] == "unavailable" and doc["obligations"] == []
    assert value["data"]["documents_stale"] == 0


def test_hourly_grid_freshness_uses_observation_not_recent_download():
    source = state(result("ercot", observed=stamp(NOW - timedelta(days=10))))
    actual = pipeline.health(source, NOW, pipeline.POLICIES["ercot"])
    assert actual["status"] == "stale"
    annual = state(result("egrid", observed="2023-12-31"))
    assert pipeline.health(annual, NOW, pipeline.POLICIES["egrid"])["status"] == "current"


def test_very_old_grid_observation_cannot_promote_despite_successful_fetch(tmp_path, monkeypatch):
    archive, public = LocalStore(tmp_path / "archive"), RecordingStore(tmp_path / "public")
    public.put("data/current.json", json_bytes({"release_id": "old"}))
    monkeypatch.setattr(pipeline, "POLICIES", {"ercot": {"required": True, "minimum": 1, "interval": 1, "stale": 7200}})
    called = []
    monkeypatch.setattr(pipeline, "build_release", lambda *a: called.append(True) or ({"data.json": b"{}"}, {}))
    adapter = lambda *a, **k: result("ercot", count=1, observed=stamp(NOW - timedelta(days=10)))
    with pytest.raises(ValueError, match="grace|observation|expired"):
        pipeline.run(archive, public, now=NOW, adapter_map={"ercot": adapter})
    assert not called
    assert read_json(public, "data/current.json")["release_id"] == "old"
    assert read_json(public, "data/pipeline-status.json")["status"] == "failed"


def test_failed_optional_source_isolated_and_required_missing_blocks_promotion(tmp_path, monkeypatch):
    archive, public = LocalStore(tmp_path / "archive"), LocalStore(tmp_path / "public")
    monkeypatch.setattr(pipeline, "POLICIES", {
        "echo": {"required": True, "minimum": 1, "interval": 1, "stale": 7200},
        "osm": {"required": False, "minimum": 1, "interval": 1, "stale": 7200}})
    seen = []
    monkeypatch.setattr(pipeline, "build_release", lambda states, *a: seen.append(states) or ({"cards.json": b"{}"}, {}))

    def fail(*a, **k):
        raise ValueError("source refused request")

    report = pipeline.run(archive, public, now=NOW, adapter_map={"echo": lambda *a, **k: result(count=1), "osm": fail})
    assert report["promoted"] and report["status"] == "degraded"
    assert seen[0]["echo"]["status"] == "current" and seen[0]["osm"]["status"] == "failed"
    old = public.get("data/current.json")
    empty_archive = LocalStore(tmp_path / "empty")
    with pytest.raises(ValueError, match="Required sources unavailable"):
        pipeline.run(empty_archive, public, now=NOW + timedelta(seconds=2), adapter_map={"echo": fail, "osm": fail})
    assert public.get("data/current.json") == old


def test_promotion_is_last_and_manifest_hashes_match_decompressed_objects(tmp_path):
    public = RecordingStore(tmp_path)
    manifest = {"release_id": "r1", "base_path": "/data/releases/r1"}
    original = {"a.json": b'{"a":1}', "b.json": b'{"b":2}'}
    actual = pipeline.publish(public, original, manifest)
    assert public.writes[-2:] == ["data/releases/r1/manifest.json", "data/current.json"]
    for key, expected in original.items():
        compressed = public.get("data/releases/r1/" + key)[0]
        assert gzip.decompress(compressed) == expected
        assert actual["objects"][key] == {"sha256": digest(expected), "bytes": len(expected), "gzip_bytes": len(compressed)}
    assert read_json(public, "data/current.json") == read_json(public, "data/releases/r1/manifest.json")


def test_upload_failure_never_changes_current_manifest(tmp_path):
    public = RecordingStore(tmp_path, fail_at="data/releases/r1/z.json")
    public.put("data/current.json", b'{"release_id":"old"}')
    old = public.get("data/current.json")
    with pytest.raises(OSError):
        pipeline.publish(public, {"a.json": b"{}", "z.json": b"{}"},
                         {"base_path": "/data/releases/r1"}, previous=old)
    assert public.get("data/current.json") == old
    assert public.get("data/releases/r1/manifest.json") is None


def test_concurrent_publisher_cannot_overwrite_winning_release(tmp_path):
    public = RecordingStore(tmp_path)
    public.put("data/current.json", b'{"release_id":"old"}')
    stale_token = public.get("data/current.json")
    public.put("data/current.json", b'{"release_id":"winner"}', expected=stale_token[1])
    with pytest.raises(ConflictError):
        pipeline.publish(public, {"a.json": b"{}"}, {"base_path": "/data/releases/loser"}, previous=stale_token)
    assert read_json(public, "data/current.json")["release_id"] == "winner"


def test_store_immutable_objects_and_path_boundaries(tmp_path):
    store = LocalStore(tmp_path)
    token = store.put("immutable.json", b"first", create_only=True)
    assert store.put("immutable.json", b"first", create_only=True) == token
    with pytest.raises(ConflictError):
        store.put("immutable.json", b"second", create_only=True)
    with pytest.raises(ConflictError):
        store.put("missing.json", b"second", expected=token)
    with pytest.raises(ValueError):
        store.put("../outside", b"bad")


def test_s3_store_sets_real_conditional_headers_and_verifies_upload():
    class Client:
        def put_object(self, **kwargs):
            self.args = kwargs
            return {"ETag": '"new"'}

        def head_object(self, **kwargs):
            return {"Metadata": self.args["Metadata"], "ContentLength": len(self.args["Body"])}

    store = object.__new__(S3Store)
    store.bucket, store.client = "test", Client()
    store.put("current.json", b"new", expected='"old"')
    assert store.client.args["IfMatch"] == '"old"'
    store.put("immutable.json", b"new", create_only=True, content_encoding="gzip")
    assert store.client.args["IfNoneMatch"] == "*" and store.client.args["ContentEncoding"] == "gzip"
    store.client.head_object = lambda **kwargs: {"Metadata": {"sha256": "bad"}, "ContentLength": 3}
    with pytest.raises(RuntimeError, match="verification"):
        store.put("broken.json", b"new")


def cache_receipt(store, url, raw=b"PDF bytes", *, retrieved=NOW - timedelta(days=1)):
    sha = digest(raw)
    receipt = {"url": url, "retrieved_at": stamp(retrieved), "sha256": sha,
               "raw_key": f"raw/{sha}.gz", "etag": '"upstream-version"'}
    store.put(receipt["raw_key"], gzip.compress(raw))
    store.put("http/" + digest(url.encode()) + ".json", json_bytes(receipt))
    return receipt


def test_cached_document_preserves_original_retrieval_time_and_validates_hash(tmp_path, monkeypatch):
    store, url = LocalStore(tmp_path), "https://publisher.example/file.pdf"
    original = cache_receipt(store, url)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("fresh cached document was refetched"))
    fetcher = CaptureFetcher(store, NOW)
    assert fetcher.get_bytes(url) == b"PDF bytes"
    assert fetcher.receipt(url)["retrieved_at"] == original["retrieved_at"]
    store.put(original["raw_key"], gzip.compress(b"changed bytes"))
    with pytest.raises(ValueError, match="digest mismatch"):
        CaptureFetcher(store, NOW).get_bytes(url)


def test_http_304_revalidation_keeps_retrieval_date_and_records_check(tmp_path, monkeypatch):
    store, url = LocalStore(tmp_path), "https://publisher.example/list.json"
    original = cache_receipt(store, url, b'{"rows":1}')

    def unchanged(req, **kwargs):
        assert req.headers["If-none-match"] == '"upstream-version"'
        raise urllib.error.HTTPError(url, 304, "unchanged", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", unchanged)
    fetcher = CaptureFetcher(store, NOW)
    assert fetcher.get_json(url) == {"rows": 1}
    assert fetcher.receipt(url)["retrieved_at"] == original["retrieved_at"]
    assert fetcher.receipt(url)["checked_at"] == stamp(NOW)


def test_reader_rate_limit_honors_retry_after_without_burst_or_blocking_cached_bytes(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    cached_url = "https://r.jina.ai/https://publisher.example/cached.pdf"
    cache_receipt(store, cached_url)
    calls = []
    monkeypatch.setattr("time.monotonic", lambda: 100.0)
    monkeypatch.setattr("time.sleep", lambda n: pytest.fail("429 must defer the batch, not sleep/retry"))

    def limited(req, **kwargs):
        calls.append(req.full_url)
        headers = Message()
        headers["Retry-After"] = "120"
        raise urllib.error.HTTPError(req.full_url, 429, "Too many requests", headers, None)

    monkeypatch.setattr("urllib.request.urlopen", limited)
    fetcher = CaptureFetcher(store, NOW)
    with pytest.raises(RuntimeError, match="429"):
        fetcher.get_bytes("https://r.jina.ai/https://publisher.example/new1.pdf")
    with pytest.raises(RuntimeError, match="deferred"):
        fetcher.get_bytes("https://r.jina.ai/https://publisher.example/new2.pdf")
    assert len(calls) == 1 and fetcher._reader_blocked_until == 220
    assert fetcher.get_bytes(cached_url) == b"PDF bytes"


@pytest.mark.parametrize("url", ["http://publisher.example/data", "https://user:password@publisher.example/data"])
def test_fetch_rejects_non_https_and_credentials(tmp_path, url):
    with pytest.raises(ValueError):
        CaptureFetcher(LocalStore(tmp_path), NOW).get_bytes(url)


def feature(code, rings, *, valid=True):
    return {"type": "Feature", "properties": {"subregion": code, "geometry_valid": valid},
            "geometry": {"type": "Polygon", "coordinates": rings}}


OUTER = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
HOLE = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]


def geometry(features=None, overlaps=None):
    return {"geojson": {"type": "FeatureCollection", "features": features or [feature("A", [OUTER, HOLE])]},
            "overlap_geojson": {"type": "FeatureCollection", "features": overlaps or []},
            "boundary_buffer_degrees": .01, "data_year": 2023}


def test_polygon_holes_boundaries_and_overlapping_regions_withhold_assignment():
    geo = Geometry(geometry())
    assert geo.resolve(2, 2) == ("A", "point_in_polygon")
    assert geo.resolve(5, 5)[0] is None
    assert geo.resolve(4, 5) == (None, "near_boundary_requires_verification")
    assert geo.resolve(.005, 2)[0] is None
    assert geo.resolve(None, 2) == (None, "missing_coordinates")
    assert geo.resolve(float("nan"), 2)[0] is None
    assert Geometry(geometry([feature("A", [OUTER]), feature("B", [OUTER])])).resolve(2, 2) == (None, "ambiguous")
    assert Geometry(geometry(overlaps=[feature(None, [OUTER])])).resolve(2, 2) == (None, "multiple_subregions_require_utility")


def test_invalid_overlap_mask_blocks_whole_geographic_binding_lane():
    geo = Geometry(geometry(overlaps=[feature(None, [OUTER], valid=False)]))
    assert geo.resolve(2, 2) == (None, "geometry_unavailable")


def test_reported_coordinate_error_can_block_otherwise_interior_assignment():
    geo = Geometry(geometry())
    assert geo.resolve(2, .1) == ("A", "point_in_polygon")
    assert geo.resolve(2, .1, accuracy_m=20000) == (None, "near_boundary_requires_verification")


def build_states(facilities=None):
    facilities = facilities or [{"id": "echo:110000000001", "name": "Example site", "state": "TX",
                                "lat": 2, "lon": 2, "address_line": "1 Road", "air_records": []}]
    rate = {"name": "Example grid", "data_year": 2023, "co2e_rate_lb_per_mwh": 1000,
            "workbook_sha256": "a" * 64}
    return {"echo": state(result(data={"facilities": facilities}, count=len(facilities))),
            "egrid": state(result("egrid", data={"rates": {"A": rate}}, count=1, observed="2023-12-31")),
            "egrid_geometry": state(result("egrid_geometry", data=geometry(), count=1))}


def test_scenarios_keep_independent_scope_vintage_receipts_and_no_fake_measurements():
    files, coverage = build_release(build_states(), "release", stamp(NOW))
    card = json.loads(files["cards.json"])["echo_110000000001"]
    claims = {x["id"]: x for x in card["claims"]}
    for cid in ["power-mw", "facility-carbon", "water-gal", "diesel-mw"]:
        assert claims[cid]["value"] == "NOT_PUBLISHED" and claims[cid]["as_of"] is None
    carbon = claims["scenario-carbon"]
    assert carbon["scope"] == carbon["range_kind"] == "scenario"
    assert carbon["as_of"] == "2023-12-31" and card["as_of"] == stamp(NOW)
    assert carbon["value"] == pytest.approx(10 * 8760 * .65 * 1000 / 2204.622621848776, abs=.1)
    assert set(carbon["range"]) == {"low", "base", "high"}
    assert carbon["range"]["low"] < carbon["value"] == carbon["range"]["base"] < carbon["range"]["high"]
    assert carbon["inputs"]["factor_year"] == 2023 and carbon["inputs"]["facility_measurement"] is False
    citation = carbon["citations"][0]
    evidence = files[citation["url"].split("/release/", 1)[1]]
    assert citation["doc_sha256"] == digest(evidence)
    assert citation["quote"] == evidence.decode().strip()
    assert card["hourly"] is None
    assert coverage["actual_power_records"] == coverage["actual_water_records"] == 0


@pytest.mark.parametrize("coordinate,rate", [(None, 1000), (2, 1000), (2, 0)])
def test_operator_missing_values_are_null_and_a_computed_zero_is_preserved(coordinate, rate):
    states = build_states([{"id": "echo:110000000001", "name": "Example", "lat": coordinate, "lon": coordinate}])
    states["egrid"]["result"]["data"]["rates"]["A"]["co2e_rate_lb_per_mwh"] = rate
    files, _ = build_release(states, "release", stamp(NOW))
    operator = json.loads(files["operators.json"])["echo_110000000001"]
    methods = {m["key"]: m for m in operator["methods"]}
    assert methods["market"]["tonnes"] is None and methods["hourly"]["tonnes"] is None
    assert operator["matched_pct"] is None and operator["peak"]["pct"] is None
    assert all(c["pct"] is None for c in operator["compliance"])
    if coordinate is None:
        assert methods["location"]["tonnes"] is None
    elif rate == 0:
        assert methods["location"]["tonnes"] == 0
    else:
        assert methods["location"]["tonnes"] > 0
    assert operator["heatmap"]["hours"] == 0 and operator["heatmap"]["cells"] == []


def test_similar_names_do_not_collapse_source_ids_and_duplicate_slug_fails():
    facilities = [{"id": "echo:110000000001", "name": "Same", "lat": None, "lon": None},
                  {"id": "echo:110000000002", "name": "Same", "lat": None, "lon": None}]
    files, coverage = build_release(build_states(facilities), "release", stamp(NOW))
    assert len(json.loads(files["cards.json"])) == 2 and coverage["with_grid_binding"] == 0
    facilities[1]["id"] = "echo_110000000001"
    with pytest.raises(ValueError, match="Duplicate published facility identity"):
        build_release(build_states(facilities), "release", stamp(NOW))


def test_virginia_identity_requires_county_registration_and_rejects_even_unique_name():
    permits = [{"site": "Unique", "registration": "74269-2", "county": "Loudoun Co."},
               {"site": "Repeated", "registration": "74270-1", "county": "Loudoun Co."},
               {"site": "Repeated", "registration": "74271-1", "county": "Loudoun Co."}]
    assert _permit_matches({"state": "VA", "name": "Different", "air_records": [{"SourceID": "VA0000005110774269"}]}, permits) == permits[:1]
    assert _permit_matches({"state": "VA", "name": "Different", "air_records": [{"SourceID": "VA0000005115374269"}]}, permits) == []
    assert _permit_matches({"state": "VA", "name": "Repeated"}, permits) == []
    assert _permit_matches({"state": "VA", "name": "Unique"}, permits) == []
    assert _permit_matches({"state": "TX", "name": "Unique"}, permits) == []


def test_archived_permit_scope_dates_and_representation_survive_cards_and_drawers():
    states = build_states([{"id": "echo:110000000001", "name": "Unique", "state": "VA", "lat": None, "lon": None,
                            "air_records": [{"SourceID": "VA0000005110774269"}]}])
    citation = {"content_sha256": "b" * 64, "quote": "(12) emergency generators each rated 2500 ekW", "page": 2,
                "representation": "reader_text", "page_derivation": "printed page header"}
    doc = {"status": "parsed", "extraction_version": "va-deterministic-3", "content_sha256": "b" * 64,
           "archive_source": "retained_repository_reader_text", "representation": "reader_text",
           "retrieved_at": None, "archived_by_at": "2026-10-28T06:05:40Z",
           "obligations": [{"metric": "generator_hours", "value": 100, "unit": "h/year", "scope": "quoted_condition",
                            "applies_to_entire_site": False, "citation": citation}],
           "equipment": [{"mw_total": 30, "citations": [citation]}]}
    permit = {"registration": "74269-2", "site": "Unique", "county": "Loudoun Co.", "issued_iso": "2025-12-01",
              "permit_url": "https://publisher.example/permit", "document": doc}
    states["virginia_permits"] = state(result("virginia_permits", data={"permits": [permit]}, count=1))
    files, _ = build_release(states, "release", stamp(NOW))
    card = json.loads(files["cards.json"])["echo_110000000001"]
    limit = card["facility"]["permit_limits"][0]
    assert limit["freshness"] == "archived" and limit["retrieved_at"] is None
    assert limit["archived_by_at"] == doc["archived_by_at"] and limit["scope"] == "quoted_condition"
    assert limit["applies_to_entire_site"] is False and limit["representation"] == "reader_text"
    diesel = next(c for c in card["claims"] if c["id"] == "diesel-mw")
    assert diesel["evidence_scope"] == "equipment_table_page" and diesel["is_complete_fleet"] is False
    assert diesel["as_of"] == "2025-12-01" and diesel["freshness"] == "archived"
    drawer = json.loads(files["drawers.json"])["echo_110000000001"]["diesel_mw"]
    assert drawer["freshness"] == drawer["citation"]["freshness"] == "archived"
    assert drawer["citation"]["representation"] == "reader_text"
    assert drawer["citation"]["retrieved_at"] is None
    assert drawer["citation"]["verified_at_utc"] is None
    assert limit["effective_status"] == "unknown" and limit["latest_effective_status_verified"] is False
    # A new failed revision must not elevate an older table to current capacity.
    states["virginia_permits"]["result"]["data"]["permits"].append({**permit, "registration": "74269-3", "issued_iso": "2026-11-01",
                                                                  "document": {"status": "failed", "obligations": []}})
    new_files, _ = build_release(states, "release2", stamp(NOW))
    new_card = json.loads(new_files["cards.json"])["echo_110000000001"]
    assert next(c for c in new_card["claims"] if c["id"] == "diesel-mw")["value"] == "NOT_PUBLISHED"


def texas_document():
    sha = "a" * 64
    url = "https://www.tceq.texas.gov/example.pdf"
    citation = {"verified": True, "document_sha256": sha, "page": 2, "url": url,
                "quote": "At full build-out, the facility features 86 diesel emergency generators."}
    return {"id": "tceq:O4791", "status": "parsed", "name": "Example", "permit_number": "O4791",
            "document_type": "draft_statement_of_basis", "document_date": "2026-02-27", "url": url,
            "content_sha256": sha, "pages": 14, "identity": {"address": "14720 Omicron Drive", "city": "San Antonio"},
            "receipt": {"sha256": sha, "retrieved_at": "2026-10-01T00:00:00Z"},
            "equipment": [{"metric": "backup_generator_count", "value": 86, "citation": citation}],
            "claims": [], "conflicts": []}


def test_texas_exact_address_keeps_draft_date_retrieval_receipt_and_quote_without_numeric_claim():
    doc = texas_document()
    found = for_facility({"state": "TX", "city": "SAN ANTONIO", "address_line": "14720 OMICRON DR"}, {"documents": [doc]})
    assert len(found) == 1
    item = found[0]
    assert item["document_date"] == "2026-02-27" and item["retrieved_at"] == "2026-10-01T00:00:00Z"
    assert item["is_current_effective_limit"] is item["latest_effective_status_verified"] is False
    assert item["identity_match"]["method"] == "exact_street_address_city_state"
    assert item["statements"][0]["quote"] == doc["equipment"][0]["citation"]["quote"]
    assert "value" not in item["statements"][0]


def test_texas_joins_exact_rn_and_rejects_conflicting_rn_fuzzy_address_or_names():
    doc = texas_document()
    doc["regulated_entity_id"] = "RN111926465"
    texas = {"documents": [doc]}
    assert for_facility({"state": "TX", "air_records": [{"OtherPermitIDs": "RN111926465 RN111000000"}]}, texas)
    assert not for_facility({"state": "TX", "city": "San Antonio", "address_line": "14720 Omicron Dr",
                             "air_records": [{"OtherPermitIDs": "RN111000000"}]}, texas)
    assert not for_facility({"state": "TX", "name": "Example"}, texas)
    assert not for_facility({"state": "TX", "city": "San Antonio", "address_line": "14721 Omicron Dr"}, texas)
    assert not for_facility({"state": "VA", "air_records": [{"OtherPermitIDs": "RN111926465"}]}, texas)


def test_texas_thresholds_identifier_counts_bad_receipts_and_unverified_quotes_are_withheld():
    doc = texas_document()
    facility = {"state": "TX", "city": "San Antonio", "address_line": "14720 Omicron Dr"}
    doc["equipment"][0]["citation"]["quote"] = "EMGEN118 EMERGENCY GENERATOR 118 GROUP ENG1A"
    assert for_facility(facility, {"documents": [doc]})[0]["statements"] == []
    doc["equipment"][0]["citation"]["quote"] = "Power rating is greater than 2237 KW."
    assert for_facility(facility, {"documents": [doc]})[0]["statements"] == []
    doc = texas_document()
    doc["equipment"][0]["citation"]["verified"] = False
    assert for_facility(facility, {"documents": [doc]})[0]["statements"] == []
    doc["receipt"]["sha256"] = "b" * 64
    assert for_facility(facility, {"documents": [doc]}) == []
