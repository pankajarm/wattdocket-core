"""Independent release review regressions: uncertainty and reproducible bytes."""
import copy
import gzip
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message

import pytest

from wattdocket.production.build import Geometry, build_release
from wattdocket.production import pipeline
from wattdocket.production.fetch import CaptureFetcher, FetchBudgetExceeded, stamp
from wattdocket.production.models import SourceResult
from wattdocket.production.storage import LocalStore, digest, json_bytes, read_json


STAMP = "2026-11-03T19:40:00Z"


def geometry(*, overlap=False):
    def feature(code, rings):
        return {"type": "Feature", "properties": {"subregion": code, "geometry_valid": True},
                "geometry": {"type": "Polygon", "coordinates": rings}}
    outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    overlap_ring = [[4, 1], [6, 1], [6, 3], [4, 3], [4, 1]]
    return {"geojson": {"type": "FeatureCollection", "features": [feature("TEST", [outer, hole])]},
            "overlap_geojson": {"type": "FeatureCollection", "features": [feature(None, [overlap_ring])] if overlap else []},
            "boundary_buffer_degrees": .01, "data_year": 2023}


def states():
    rate = {"name": "Test grid", "data_year": 2023, "co2e_rate_lb_per_mwh": 1000,
            "workbook_sha256": "a" * 64}
    facility = {"id": "echo:110000000001", "name": "Test facility", "state": "TX", "lat": 2, "lon": 2}
    data = {"echo": {"facilities": [facility]}, "egrid": {"rates": {"TEST": rate}},
            "egrid_geometry": geometry()}
    return {sid: {"id": sid, "status": "current", "retrieved_at": STAMP,
                  "result": {"source_id": sid, "data": payload, "url": "https://example.test/" + sid,
                             "observation_end": "2024-01-01T00:00:00Z"}, "result_sha256": "b" * 64}
            for sid, payload in data.items()}


def test_thread_completion_order_cannot_change_public_release_bytes():
    source_states = states()
    untouched = copy.deepcopy(source_states)
    first = build_release(source_states, "fixed-release", STAMP)
    repeated = build_release(source_states, "fixed-release", STAMP)
    reversed_completion = build_release(dict(reversed(list(source_states.items()))), "fixed-release", STAMP)
    assert first == repeated == reversed_completion
    assert source_states == untouched
    assert json.loads(first[0]["cards.json"])["echo_110000000001"]["claims"][-1]["value"] > 0


@pytest.mark.parametrize("geometry_year", [2023, None])
def test_mismatched_annual_grid_edition_withholds_facility_and_address_assignments(geometry_year):
    source_states = states()
    source_states["egrid"]["result"]["data"]["rates"]["TEST"]["data_year"] = 2024
    source_states["egrid_geometry"]["result"]["data"]["data_year"] = geometry_year
    files, coverage = build_release(source_states, "new-edition", STAMP)
    card = json.loads(files["cards.json"])["echo_110000000001"]
    assert card["grid"]["subregion"] is None
    assert not any(c["id"] == "scenario-carbon" for c in card["claims"])
    assert coverage["with_grid_binding"] == 0
    assert "egrid-subregions.json" not in files


def test_new_annual_edition_refreshes_geometry_before_monthly_cadence(tmp_path, monkeypatch):
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    archive, public = LocalStore(tmp_path / "archive"), LocalStore(tmp_path / "public")
    previous = states()["egrid_geometry"]
    previous.update(last_attempt_at=stamp(now - timedelta(days=1)), last_success_at=stamp(now - timedelta(days=1)), record_count=1)
    archive.put("state/sources/egrid_geometry.json", json_bytes(previous))
    policies = {sid: {"required": True, "minimum": 1, "interval": 30 * 86400, "stale": 45 * 86400}
                for sid in ("egrid", "egrid_geometry")}
    monkeypatch.setattr(pipeline, "POLICIES", policies)
    calls, seen = [], []
    annual = lambda *a, **k: SourceResult("egrid", "Annual", "https://example.test", {"rates": {"TEST": {"data_year": 2024}}}, 1)

    def new_geometry(*args, **kwargs):
        calls.append(True)
        return SourceResult("egrid_geometry", "Boundaries", "https://example.test", {**geometry(), "data_year": 2024}, 1)

    monkeypatch.setattr(pipeline, "build_release", lambda source_states, *args: seen.append(source_states) or ({"data.json": b"{}"}, {}))
    pipeline.run(archive, public, now=now, selected=["egrid"], adapter_map={"egrid": annual, "egrid_geometry": new_geometry})
    assert calls == [True]
    assert seen[0]["egrid_geometry"]["result"]["data"]["data_year"] == 2024
    assert read_json(public, "data/current.json")["release_id"]


@pytest.mark.parametrize("lat,lon", [(2, .015), (5, 3.985)])
def test_coordinate_and_geometry_error_bounds_add_at_exteriors_and_holes(lat, lon):
    geo = Geometry(geometry())
    assert geo.resolve(lat, lon) == ("TEST", "point_in_polygon")
    # Each bound alone is below the 0.015-degree clearance. Their sum is not.
    assert geo.resolve(lat, lon, 666) == (None, "near_boundary_requires_verification")


def test_combined_uncertainty_also_excludes_nearby_utility_overlap():
    geo = Geometry(geometry(overlap=True))
    assert geo.resolve(2, 3.985) == ("TEST", "point_in_polygon")
    assert geo.resolve(2, 3.985, 666) == (None, "multiple_subregions_require_utility")


@pytest.mark.parametrize("lat,lon", [(True, 2), (2, False), (91, 2), (2, 181)])
def test_invalid_coordinates_cannot_receive_geographic_context(lat, lon):
    assert Geometry(geometry()).resolve(lat, lon) == (None, "missing_coordinates")


@pytest.mark.parametrize("accuracy", [True, "500", float("inf"), -1])
def test_invalid_accuracy_does_not_silently_become_exact_position(accuracy):
    assert Geometry(geometry()).resolve(2, 2, accuracy) == (None, "invalid_coordinate_accuracy")


def test_http_304_with_expired_raw_object_recovers_with_bounded_unconditional_request(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    url, raw = "https://publisher.example/annual-data", b'newly fetched body'
    receipt = {"url": url, "retrieved_at": "2024-01-01T00:00:00Z", "raw_key": "raw/expired.gz",
               "sha256": "a" * 64, "etag": '"still-unchanged"'}
    store.put("http/" + digest(url.encode()) + ".json", json_bytes(receipt))
    calls = []

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(raw)
            self.url = url
            self.headers = Message()
            self.headers["Content-Type"] = "application/octet-stream"
            self.headers["ETag"] = '"still-unchanged"'

    def open_request(request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            assert request.get_header("If-none-match") == receipt["etag"]
            raise urllib.error.HTTPError(url, 304, "unchanged", {}, None)
        assert request.get_header("If-none-match") is None
        assert request.get_header("If-modified-since") is None
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    fetcher = CaptureFetcher(store, datetime(2026, 11, 3, tzinfo=timezone.utc), attempts=2)
    assert fetcher.get_bytes(url) == raw
    assert len(calls) == 2
    refreshed = fetcher.receipt(url)
    assert refreshed["retrieved_at"] == "2026-11-03T00:00:00Z"
    assert gzip.decompress(store.get(refreshed["raw_key"])[0]) == raw


@pytest.mark.parametrize("corrupt", [False, True])
def test_raw_archive_identity_uses_publisher_bytes_across_gzip_encoders(tmp_path, monkeypatch, corrupt):
    store = LocalStore(tmp_path)
    url, raw = "https://publisher.example/records.json", b'{"source":"unchanged"}'
    key = "raw/" + digest(raw) + ".gz"
    first_encoding = gzip.compress(b"different evidence" if corrupt else raw, mtime=123)
    store.put(key, first_encoding, create_only=True)

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(raw)
            self.url, self.headers = url, Message()
            self.headers["Content-Type"] = "application/json"

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response())
    fetcher = CaptureFetcher(store, datetime(2026, 11, 3, tzinfo=timezone.utc), attempts=1)
    if corrupt:
        with pytest.raises(ValueError, match="content-addressed key"):
            fetcher.get_bytes(url)
        assert fetcher.receipt(url) is None
    else:
        assert fetcher.get_bytes(url) == raw
        assert fetcher.receipt(url)["sha256"] == digest(raw)
    assert store.get(key)[0] == first_encoding  # Neither case rewrites evidence.


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class BudgetResponse(io.BytesIO):
    def __init__(self, url, raw=b"complete response"):
        super().__init__(raw)
        self.url, self.headers = url, Message()
        self.headers["Content-Type"] = "application/octet-stream"


def install_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("wattdocket.production.fetch.time.monotonic", clock.monotonic)
    monkeypatch.setattr("wattdocket.production.fetch.time.sleep", clock.sleep)
    return clock


def test_multiple_urls_share_one_source_deadline_and_cached_documents_survive(tmp_path, monkeypatch):
    clock, store = install_clock(monkeypatch), LocalStore(tmp_path)
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    cached_url, cached = "https://publisher.example/cached.pdf", b"cached evidence"
    sha = digest(cached)
    store.put(f"raw/{sha}.gz", gzip.compress(cached))
    store.put("http/" + digest(cached_url.encode()) + ".json", json_bytes(
        {"url": cached_url, "retrieved_at": stamp(now), "raw_key": f"raw/{sha}.gz", "sha256": sha}))
    timeouts = []

    def request(req, *, timeout, **kwargs):
        timeouts.append(timeout)
        clock.now += min(8, timeout)
        return BudgetResponse(req.full_url)

    monkeypatch.setattr("urllib.request.urlopen", request)
    fetcher = CaptureFetcher(store, now, source_budget_seconds=20, attempts=1)
    assert fetcher.get_bytes("https://publisher.example/first")
    assert fetcher.get_bytes("https://publisher.example/second")
    with pytest.raises(FetchBudgetExceeded):
        fetcher.get_bytes("https://publisher.example/third")
    with pytest.raises(FetchBudgetExceeded):
        fetcher.get_bytes("https://publisher.example/fourth")
    assert timeouts == [20, 12, 4]
    assert fetcher.get_bytes(cached_url) == cached
    assert len(timeouts) == 3  # Cache access spends no new upstream request.


def test_retry_backoff_cannot_extend_the_source_deadline(tmp_path, monkeypatch):
    clock, calls = install_clock(monkeypatch), []

    def fail(*args, **kwargs):
        calls.append(kwargs["timeout"])
        clock.now += .75
        raise TimeoutError("timeout")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    fetcher = CaptureFetcher(LocalStore(tmp_path), datetime(2026, 11, 3, tzinfo=timezone.utc), source_budget_seconds=1)
    with pytest.raises(FetchBudgetExceeded):
        fetcher.get_bytes("https://publisher.example/data")
    assert calls == [1] and clock.sleeps == []


def test_slow_response_reads_recheck_budget_and_never_archive_partial_body(tmp_path, monkeypatch):
    from types import SimpleNamespace
    clock, timeouts = install_clock(monkeypatch), []
    store = LocalStore(tmp_path)

    class SlowResponse(BudgetResponse):
        def __init__(self, url):
            super().__init__(url)
            self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append)))

        def read1(self, size):
            clock.now += 6
            return b"partial"

    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kwargs: SlowResponse(req.full_url))
    fetcher = CaptureFetcher(store, datetime(2026, 11, 3, tzinfo=timezone.utc), source_budget_seconds=10)
    with pytest.raises(FetchBudgetExceeded):
        fetcher.get_bytes("https://publisher.example/slow")
    assert timeouts == [10, 4]
    assert fetcher.receipts == {}
    assert not list(tmp_path.glob("raw/*"))


def test_overpass_query_gets_server_time_but_leaves_bounded_sequential_fallback(tmp_path, monkeypatch):
    clock, calls = install_clock(monkeypatch), []

    def timeout(req, *, timeout, **kwargs):
        calls.append((req.host, timeout))
        clock.now += timeout
        raise TimeoutError("query timed out")

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    fetcher = CaptureFetcher(LocalStore(tmp_path), datetime(2026, 11, 3, tzinfo=timezone.utc))
    for host in ("overpass-api.de", "overpass.kumi.systems"):
        with pytest.raises(RuntimeError):
            fetcher.get_bytes(f"https://{host}/api/interpreter")
    assert calls == [("overpass-api.de", 110), ("overpass.kumi.systems", 40)]
    assert clock.sleeps == [] and clock.now == 150
