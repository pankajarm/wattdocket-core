"""A failed public-data refresh must never replace the last verified snapshot."""
from concurrent.futures import ThreadPoolExecutor
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_snapshot", ROOT / "scripts/sync_public_data.py")
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


def release(rid="20261106T120000Z-fixture", value=1):
    files = {"facilities.json": json.dumps({"release_id": rid, "results": [], "value": value}).encode(),
             "grid/index.json": json.dumps({"release_id": rid, "regions": []}).encode()}
    manifest = {"schema_version": "2.0", "release_id": rid, "built_at": "2026-11-06T12:00:00Z",
                "base_path": f"/data/releases/{rid}", "objects": {
                    name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                    for name, raw in files.items()}}
    return manifest, files


def downloader(manifest, files, *, broken=None, compressed=True):
    def download(url, limit=None):
        if url.endswith("/data/current.json"):
            raw = json.dumps(manifest).encode()
        else:
            name = url.split(manifest["base_path"] + "/", 1)[1]
            raw = broken if name == "facilities.json" and broken is not None else files[name]
        return (gzip.compress(raw, mtime=0), "gzip") if compressed else (raw, "")
    return download


def seed(root, monkeypatch, rid="old-release"):
    manifest, files = release(rid)
    monkeypatch.setattr(snapshot, "download", downloader(manifest, files))
    snapshot.sync(root, "https://snapshot.example")
    return manifest


@pytest.mark.parametrize("rid", [".", "..", "../outside", "a/b", "/tmp", "", "a" * 97, "bad.release"])
def test_release_ids_cannot_escape_data_root(rid):
    manifest, _ = release(rid)
    with pytest.raises(ValueError, match="release id"):
        snapshot.validate_manifest(manifest)


@pytest.mark.parametrize("name", ["../escape.json", "/escape.json", "grid/../escape.json", "grid//index.json", "grid/data.txt"])
def test_object_paths_are_relative_json_files(name):
    manifest, _ = release()
    manifest["objects"] = {name: {"bytes": 1, "sha256": "0" * 64}}
    with pytest.raises(ValueError, match="object path"):
        snapshot.validate_manifest(manifest)


def test_manifest_shapes_and_sizes_are_bounded():
    manifest, _ = release()
    for invalid in (None, [], {}, "bad"):
        with pytest.raises(ValueError):
            snapshot.validate_manifest(invalid)
    for size in (True, 0, -1, snapshot.MAX_OBJECT_BYTES + 1):
        bad = copy.deepcopy(manifest)
        bad["objects"]["facilities.json"]["bytes"] = size
        with pytest.raises(ValueError, match="object size"):
            snapshot.validate_manifest(bad)
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        snapshot.decode_json(b'{"release_id":"one","release_id":"two"}')
    with pytest.raises(ValueError, match="Non-finite"):
        snapshot.decode_json(b'{"value":NaN}')


@pytest.mark.parametrize("compressed", [True, False])
def test_sync_and_verify_are_complete_and_offline(tmp_path, monkeypatch, compressed):
    manifest, files = release()
    monkeypatch.setattr(snapshot, "download", downloader(manifest, files, compressed=compressed))
    assert snapshot.sync(tmp_path, "https://snapshot.example") == manifest
    monkeypatch.setattr(snapshot, "download", lambda *args: pytest.fail("Offline verification contacted network"))
    assert snapshot.verify_snapshot(tmp_path) == manifest
    for name, data in files.items():
        assert gzip.decompress((tmp_path / "releases" / manifest["release_id"] / (name + ".gz")).read_bytes()) == data
    assert not list((tmp_path / "releases").glob(".download-*"))


def test_corrupt_download_preserves_last_good_pointer_and_files(tmp_path, monkeypatch):
    old = seed(tmp_path, monkeypatch)
    old_pointer = (tmp_path / "current.json").read_bytes()
    manifest, files = release("new-release")
    monkeypatch.setattr(snapshot, "download", downloader(manifest, files, broken=b'{}'))
    with pytest.raises(ValueError, match="Checksum or size mismatch"):
        snapshot.sync(tmp_path, "https://snapshot.example")
    assert (tmp_path / "current.json").read_bytes() == old_pointer
    assert snapshot.verify_snapshot(tmp_path) == old
    assert not (tmp_path / "releases" / "new-release").exists()
    assert not list((tmp_path / "releases").glob(".download-*"))


def test_same_release_cannot_replace_different_immutable_bytes(tmp_path, monkeypatch):
    old = seed(tmp_path, monkeypatch, "unchanged-id")
    old_pointer = (tmp_path / "current.json").read_bytes()
    manifest, files = release("unchanged-id", value=2)
    monkeypatch.setattr(snapshot, "download", downloader(manifest, files))
    with pytest.raises(ValueError, match="differs from immutable manifest"):
        snapshot.sync(tmp_path, "https://snapshot.example")
    assert (tmp_path / "current.json").read_bytes() == old_pointer
    assert snapshot.verify_snapshot(tmp_path) == old


def test_offline_verification_rejects_corrupt_bytes_and_pointer_mismatch(tmp_path, monkeypatch):
    manifest = seed(tmp_path, monkeypatch)
    release_dir = tmp_path / "releases" / manifest["release_id"]
    (release_dir / "facilities.json.gz").write_bytes(gzip.compress(b'{}'))
    with pytest.raises(ValueError, match="Checksum or size mismatch"):
        snapshot.verify_snapshot(tmp_path)
    changed = copy.deepcopy(manifest)
    changed["built_at"] = "2099-01-01T00:00:00Z"
    (tmp_path / "current.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="differs from immutable manifest"):
        snapshot.verify_snapshot(tmp_path)


def test_decoder_bounds_compressed_bombs_and_rejects_invalid_encoding():
    with pytest.raises(ValueError, match="Decoded content exceeded"):
        snapshot.decoded_bytes(gzip.compress(b"x" * 1_000_000), "gzip", 100)
    with pytest.raises(ValueError, match="Invalid gzip"):
        snapshot.decoded_bytes(b"bad gzip", "gzip", 100)
    with pytest.raises(ValueError, match="Unsupported content encoding"):
        snapshot.decoded_bytes(b"{}", "br", 100)


def test_symlink_escape_is_rejected_without_touching_outside(tmp_path, monkeypatch):
    root, outside = tmp_path / "snapshot", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (root / "releases").symlink_to(outside, target_is_directory=True)
    manifest, files = release()
    monkeypatch.setattr(snapshot, "download", downloader(manifest, files))
    with pytest.raises(ValueError, match="escapes"):
        snapshot.sync(root, "https://snapshot.example")
    assert not list(outside.iterdir())
    assert not (root / "current.json").exists()


def test_concurrent_syncs_use_distinct_atomic_pointer_files(tmp_path, monkeypatch):
    first, first_files = release("release-a")
    second, second_files = release("release-b")
    downloads = {"a": downloader(first, first_files), "b": downloader(second, second_files)}
    def download(url, limit=None):
        return downloads["a" if url.startswith("https://a.example") else "b"](url, limit)
    monkeypatch.setattr(snapshot, "download", download)
    replace = snapshot.os.replace
    barrier = threading.Barrier(2)
    sources = []
    def concurrent_replace(source, destination):
        sources.append(str(source))
        barrier.wait(timeout=5)
        replace(source, destination)
    monkeypatch.setattr(snapshot.os, "replace", concurrent_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda origin: snapshot.sync(tmp_path, origin), ["https://a.example", "https://b.example"]))
    assert {r["release_id"] for r in results} == {"release-a", "release-b"}
    assert len(set(sources)) == 2
    assert snapshot.verify_snapshot(tmp_path)["release_id"] in {"release-a", "release-b"}
    assert not list(tmp_path.glob(".current-*.json.tmp"))
