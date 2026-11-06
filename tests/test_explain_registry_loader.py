"""Trusted S3 loading, immutable releases and bounded cache failures."""
from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api/lambda_explain"))
import registry_loader as loader

RID = "20261104T230000Z-test"


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def fixture(release_id=RID):
    registry = json.loads((ROOT / "tests/fixtures/explain_registry.json").read_text())
    registry["release_id"] = release_id
    raw = encoded(registry)
    compressed = gzip.compress(raw, mtime=0)
    manifest = {"schema_version": "2.2", "release_id": release_id, "built_at": registry["built_at"],
                "base_path": "/data/releases/" + release_id,
                "objects": {"explain-registry.json": {"sha256": hashlib.sha256(raw).hexdigest(),
                            "bytes": len(raw), "gzip_bytes": len(compressed)}}}
    prefix = "data/releases/" + release_id + "/"
    return registry, manifest, {"data/current.json": encoded(manifest),
        prefix + "manifest.json": encoded(manifest), prefix + "explain-registry.json": compressed}


class S3:
    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def get_object(self, *, Bucket, Key):
        self.calls.append((Bucket, Key))
        raw = self.objects[Key]
        return {"Body": io.BytesIO(raw), "ContentLength": len(raw)}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    loader.clear_cache()
    monkeypatch.setenv("WATTDOCKET_EXPLAIN_DATA_BUCKET", "wattdocket-test-data")
    yield
    loader.clear_cache()


def test_pinned_release_reads_only_fixed_s3_paths_and_checks_immutable_hash():
    registry, _, objects = fixture()
    s3 = S3(objects)
    assert loader.load_registry(RID, client=s3) == registry
    assert s3.calls == [("wattdocket-test-data", "data/releases/" + RID + "/manifest.json"),
                        ("wattdocket-test-data", "data/releases/" + RID + "/explain-registry.json")]


def test_independent_permit_document_subject_has_its_own_exact_namespace():
    registry, manifest, _ = fixture()
    item = deepcopy(next(iter(registry["subjects"].values())))
    item.update(kind="permit_document", id="74094-1", title="Virginia permit 74094-1")
    registry["subjects"]["permit_document:74094-1"] = item
    assert loader.validate_registry(registry, manifest)["subjects"]["permit_document:74094-1"] == item
    assert loader.resolve_subject(registry, "permit_document", "74094-1") == item
    with pytest.raises(loader.RegistryError, match="UNKNOWN_SUBJECT"):
        loader.resolve_subject(registry, "facility", "74094-1")



@pytest.mark.parametrize("rid", ["../secrets", "https://evil.example/facts", "/data/current.json", "", "x" * 97])
def test_client_cannot_choose_a_url_key_or_unbounded_release_id(rid):
    s3 = S3({})
    with pytest.raises(loader.RegistryError, match="INVALID_RELEASE"):
        loader.load_registry(rid, client=s3)
    assert s3.calls == []


@pytest.mark.parametrize("damage", ["hash", "release", "base_path", "missing", "size", "schema", "alias"])
def test_corrupt_or_cross_release_registry_is_rejected(damage):
    registry, manifest, objects = fixture()
    spec = manifest["objects"]["explain-registry.json"]
    if damage == "hash":
        spec["sha256"] = "0" * 64
    elif damage == "release":
        manifest["release_id"] = "another-release"
    elif damage == "base_path":
        manifest["base_path"] = "/other/private"
    elif damage == "missing":
        manifest["objects"] = {}
    elif damage == "size":
        spec["bytes"] = loader.MAX_REGISTRY_BYTES + 1
    else:
        if damage == "schema":
            registry["schema_version"] = "unknown"
        else:
            registry["aliases"]["attacker"] = "facility:unregistered"
        raw = encoded(registry)
        compressed = gzip.compress(raw)
        spec.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), gzip_bytes=len(compressed))
        objects["data/releases/" + RID + "/explain-registry.json"] = compressed
    objects["data/releases/" + RID + "/manifest.json"] = encoded(manifest)
    with pytest.raises(loader.RegistryError):
        loader.load_registry(RID, client=S3(objects))


def test_gzip_expansion_is_bounded_before_json_parsing(monkeypatch):
    _, manifest, objects = fixture()
    raw = b"x" * 10_000
    compressed = gzip.compress(raw)
    manifest["objects"]["explain-registry.json"] = {"sha256": hashlib.sha256(raw).hexdigest(),
                                                   "bytes": 128, "gzip_bytes": len(compressed)}
    objects["data/releases/" + RID + "/manifest.json"] = encoded(manifest)
    objects["data/releases/" + RID + "/explain-registry.json"] = compressed
    monkeypatch.setattr(loader, "MAX_REGISTRY_BYTES", 128)
    with pytest.raises(loader.RegistryError, match="REGISTRY_HASH_MISMATCH"):
        loader.load_registry(RID, client=S3(objects))


def test_cache_is_release_scoped_expires_and_never_serves_failed_refresh():
    registry, _, objects = fixture()
    clock = [0]
    s3 = S3(objects)
    assert loader.load_registry(RID, client=s3, clock=lambda: clock[0]) == registry
    assert loader.load_registry(RID, client=s3, clock=lambda: clock[0]) == registry
    assert len(s3.calls) == 2
    clock[0] = loader.CACHE_SECONDS
    s3.objects = {}
    with pytest.raises(loader.RegistryError, match="REGISTRY_UNAVAILABLE"):
        loader.load_registry(RID, client=s3, clock=lambda: clock[0])
    assert len(s3.calls) == 3


def test_new_release_evicts_old_json_tree_before_caching():
    _, _, objects = fixture()
    newer, _, new_objects = fixture("20261105T010000Z-test")
    s3 = S3({**objects, **new_objects})
    loader.load_registry(RID, client=s3)
    assert loader.load_registry(newer["release_id"], client=s3) == newer
    assert len(loader._RELEASES) == 1
    loader.load_registry(RID, client=s3)
    assert len(s3.calls) == 6


def test_unconfigured_bucket_never_uses_packaged_demo_or_client_transport(monkeypatch):
    monkeypatch.delenv("WATTDOCKET_EXPLAIN_DATA_BUCKET")
    s3 = S3({})
    with pytest.raises(loader.RegistryError, match="REGISTRY_NOT_CONFIGURED"):
        loader.load_registry(RID, client=s3)
    assert s3.calls == []


def test_json_duplicate_keys_and_nonfinite_literals_are_rejected():
    for raw in ['{"subjects":{},"subjects":{}}', '{"value":NaN}', '{"value":Infinity}']:
        with pytest.raises(loader.RegistryError):
            loader.decode(raw)
