"""Disclosure jobs are bounded and failures cannot refresh historical facts."""
from datetime import datetime, timedelta, timezone
import gzip

import pytest

from wattdocket.production import pipeline
from wattdocket.production.fetch import stamp
from wattdocket.production.models import SourceResult
from wattdocket.production.storage import LocalStore, digest, json_bytes, read_json

NOW = datetime(2026, 11, 3, 23, tzinfo=timezone.utc)
COUNTS = {"meta_actuals2024": 90, "massachusetts_energy": 14, "ghgrp": 10, "disclosure_identity": 6}


class RecordingFetcher:
    calls = []

    def __init__(self, store, now, **kwargs):
        self.receipts = {}
        self.calls.append(kwargs)


def source(sid, *, count=None, data=None):
    return SourceResult(sid, pipeline.LABELS[sid], "https://publisher.example/report", data or {"verified": True},
                        COUNTS[sid] if count is None else count,
                        observation_start="2024-01-01T00:00:00Z", observation_end="2025-01-01T00:00:00Z")


def prior_state(sid, *, at=NOW - timedelta(days=8), error=None):
    result = source(sid).as_dict()
    return {"id": sid, "label": result["label"], "result": result, "result_sha256": digest(json_bytes(result)),
            "record_count": result["record_count"], "retrieved_at": stamp(at), "last_success_at": stamp(at),
            "last_attempt_at": stamp(at), "observation_end": result["observation_end"], "error": error}


def test_new_adapters_are_real_registered_callables_with_weekly_optional_bounds():
    from wattdocket.production.sources_operator_actuals import collect_meta_actuals
    from wattdocket.production.sources_facility_reporting import collect_massachusetts_reporting, collect_ghgrp_reporting
    from wattdocket.production.facility_disclosure_identity import collect_disclosure_identity
    expected = {"meta_actuals2024": collect_meta_actuals, "massachusetts_energy": collect_massachusetts_reporting,
                "ghgrp": collect_ghgrp_reporting, "disclosure_identity": collect_disclosure_identity}
    registered = pipeline.adapters()
    assert set(registered) == set(pipeline.POLICIES) == set(pipeline.LABELS)
    for sid, adapter in expected.items():
        assert registered[sid] is adapter
        policy = pipeline.POLICIES[sid]
        assert policy["interval"] == 604800 and policy["stale"] == 2592000
        assert policy["required"] is False and policy["minimum"] == COUNTS[sid]
        assert policy["retry_interval"] == 86400
        assert policy["fetcher"]["source_budget_seconds"] == 60
        assert policy["fetcher"]["max_bytes"] <= 16_000_000
        assert policy["max_normalized_bytes"] <= 5_000_000


@pytest.mark.parametrize("sid", COUNTS)
def test_historical_disclosures_run_weekly_and_keep_observation_year(tmp_path, sid):
    store = LocalStore(tmp_path)
    old = prior_state(sid, at=NOW - timedelta(days=6))
    store.put(f"state/sources/{sid}.json", json_bytes(old))
    calls = []
    def collect(fetcher, **kwargs):
        calls.append(True)
        return source(sid)
    kept = pipeline.collect_one(sid, collect, store, NOW, fetcher_factory=RecordingFetcher)
    assert calls == [] and kept["last_success_at"] == old["last_success_at"]
    actual = pipeline.collect_one(sid, collect, store, NOW + timedelta(days=1), fetcher_factory=RecordingFetcher)
    assert calls == [True]
    assert RecordingFetcher.calls[-1] == pipeline.POLICIES[sid]["fetcher"]
    assert actual["observation_end"] == "2025-01-01T00:00:00Z" and actual["status"] == "current"
    assert actual["freshness_basis"].startswith("collection_health_only")


@pytest.mark.parametrize("sid", COUNTS)
def test_failed_disclosure_retries_daily_without_refreshing_last_good(tmp_path, sid):
    store = LocalStore(tmp_path)
    old = prior_state(sid, at=NOW - timedelta(days=2), error="upstream timeout")
    old["last_attempt_at"] = stamp(NOW - timedelta(hours=23))
    store.put(f"state/sources/{sid}.json", json_bytes(old))
    calls = []
    def fail(fetcher, **kwargs):
        calls.append(True)
        fetcher.receipts["failure"] = {"sha256": "new failed evidence"}
        raise TimeoutError("bounded annual source timeout")
    skipped = pipeline.collect_one(sid, fail, store, NOW, fetcher_factory=RecordingFetcher)
    assert calls == [] and skipped["status"] == "stale"
    actual = pipeline.collect_one(sid, fail, store, NOW + timedelta(hours=1), fetcher_factory=RecordingFetcher)
    assert calls == [True] and actual["status"] == "stale"
    assert actual["result"] == old["result"] and actual["result_sha256"] == old["result_sha256"]
    assert actual["retrieved_at"] == old["retrieved_at"] and actual["last_success_at"] == old["last_success_at"]
    assert actual["failed_attempt_receipts"] == [{"sha256": "new failed evidence"}]


@pytest.mark.parametrize("problem", ["below_minimum", "unreviewed_expansion", "oversize", "boolean_count"])
def test_disclosure_output_policy_rejects_without_losing_original_state(tmp_path, problem):
    store = LocalStore(tmp_path)
    sid = "meta_actuals2024"
    old = prior_state(sid)
    store.put(f"state/sources/{sid}.json", json_bytes(old))
    bad = source(sid, count=89 if problem == "below_minimum" else 91 if problem == "unreviewed_expansion" else True if problem == "boolean_count" else 90,
                 data={"oversized": "x" * 2_000_000} if problem == "oversize" else None)
    actual = pipeline.collect_one(sid, lambda *a, **kw: bad, store, NOW, fetcher_factory=RecordingFetcher)
    assert actual["status"] == "stale" and actual["error"]
    assert actual["result"] == old["result"] and actual["retrieved_at"] == old["retrieved_at"]
    assert read_json(store, f"state/sources/{sid}.json")["result"] == old["result"]


@pytest.mark.parametrize("failure", ["object_count", "gzip_bytes"])
def test_oversized_release_is_rejected_before_any_object_upload(tmp_path, monkeypatch, failure):
    class Store(LocalStore):
        def __init__(self, path):
            super().__init__(path)
            self.writes = []
        def put(self, key, data, **kwargs):
            self.writes.append(key)
            return super().put(key, data, **kwargs)
    public = Store(tmp_path)
    public.put("data/current.json", b'{"release_id":"retained"}')
    previous = public.get("data/current.json")
    public.writes.clear()
    if failure == "object_count":
        # 256 data objects plus their manifest would exceed the 256-object cap.
        files = {f"{i}.json": b"{}" for i in range(256)}
    else:
        monkeypatch.setattr(pipeline, "MAX_RELEASE_GZIP_BYTES", 256)
        files = {"a.json": bytes(range(256))}
    with pytest.raises(ValueError, match="bounded public"):
        pipeline.publish(public, files, {"base_path": "/data/releases/new"}, previous=previous)
    assert public.writes == []
    assert public.get("data/current.json") == previous


def test_exact_release_bounds_publish_with_reproducible_gzip(tmp_path, monkeypatch):
    public = LocalStore(tmp_path)
    files = {"a.json": b'{"value":1}', "b.json": b'{"value":2}'}
    compressed_bytes = sum(len(gzip.compress(raw, compresslevel=6, mtime=0)) for raw in files.values())
    monkeypatch.setattr(pipeline, "MAX_RELEASE_OBJECTS", 3)  # two data objects + manifest
    monkeypatch.setattr(pipeline, "MAX_RELEASE_GZIP_BYTES", compressed_bytes)
    manifest = pipeline.publish(public, files, {"release_id": "bounded", "base_path": "/data/releases/bounded"})
    assert sum(row["gzip_bytes"] for row in manifest["objects"].values()) == compressed_bytes
    assert read_json(public, "data/current.json")["release_id"] == "bounded"
