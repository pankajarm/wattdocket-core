#!/usr/bin/env python3
"""Download one public release, verify every object, then publish its local pointer.

No AWS access or model calls. Existing releases are immutable and retained.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MAX_MANIFEST_BYTES = 500_000
MAX_OBJECT_BYTES = 256 * 1024 * 1024
MAX_RELEASE_BYTES = 512 * 1024 * 1024
MAX_OBJECTS = 1000


def decode_json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f"Non-finite JSON value: {value}")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def validate_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("Invalid manifest")
    release = manifest.get("release_id", "")
    if not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", release):
        raise ValueError("Invalid release id")
    if manifest.get("base_path") != f"/data/releases/{release}":
        raise ValueError("Invalid release base path")
    if manifest.get("schema_version") not in ("2.0", "2.1", "2.2"):
        raise ValueError("Unsupported snapshot schema")
    objects = manifest.get("objects")
    if not isinstance(objects, dict) or not 1 <= len(objects) <= MAX_OBJECTS:
        raise ValueError("Release object count is outside the supported bounds")
    total = 0
    for name, info in objects.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+\.json", name)
                or ".." in name or name.startswith("/") or "//" in name):
            raise ValueError(f"Invalid object path: {name}")
        if not isinstance(info, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(info.get("sha256", ""))):
            raise ValueError(f"Missing checksum: {name}")
        if type(info.get("bytes")) is not int or not 0 < info["bytes"] <= MAX_OBJECT_BYTES:
            raise ValueError(f"Invalid object size: {name}")
        total += info["bytes"]
    if total > MAX_RELEASE_BYTES:
        raise ValueError("Release exceeds the supported total size")


def verify_object(data: bytes, info: dict, name: str) -> None:
    if len(data) != info["bytes"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
        raise ValueError(f"Checksum or size mismatch: {name}")
    decode_json(data)


def decoded_bytes(data: bytes, encoding: str, limit: int) -> bytes:
    """Bound decompression before allocating an entire gzip member."""
    if encoding == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                result = stream.read(limit + 1)
        except (OSError, EOFError) as exc:
            raise ValueError("Invalid gzip data") from exc
    elif encoding in ("", "identity"):
        result = data
    else:
        raise ValueError(f"Unsupported content encoding: {encoding}")
    if len(result) > limit:
        raise ValueError("Decoded content exceeded size limit")
    return result


def bounded_file(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Local file exceeded size limit")
    return data


def compressed_limit(info: dict) -> int:
    # Even an incompressible member has small framing overhead; this permits
    # recompressed local copies without trusting arbitrary gzip_bytes metadata.
    return min(MAX_OBJECT_BYTES + 65536, info["bytes"] + 65536)


def inside(root: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Snapshot path escapes its data root")
    return path


def verify_release(root: Path, manifest: dict) -> None:
    release_dir = inside(root, root / "releases" / manifest["release_id"])
    immutable = inside(root, release_dir / "manifest.json")
    if decode_json(bounded_file(immutable, MAX_MANIFEST_BYTES)) != manifest:
        raise ValueError("Local pointer differs from immutable manifest")
    for name, info in manifest["objects"].items():
        target = inside(root, release_dir / (name + ".gz"))
        raw = decoded_bytes(bounded_file(target, compressed_limit(info)), "gzip", info["bytes"])
        verify_object(raw, info, name)


def verify_snapshot(root: Path) -> dict:
    manifest = decode_json(bounded_file(inside(root, root / "current.json"), MAX_MANIFEST_BYTES))
    validate_manifest(manifest)
    verify_release(root, manifest)
    return manifest


def download(url: str, limit: int = MAX_OBJECT_BYTES) -> tuple[bytes, str]:
    # certifi is optional; standard Python certificate stores work elsewhere.
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    request = Request(url, headers={"Accept-Encoding": "gzip", "Accept": "application/json", "User-Agent": "WattDocket-public-snapshot/1"})
    with urlopen(request, timeout=90, context=context) as response:
        if "json" not in response.headers.get("Content-Type", ""):
            raise ValueError(f"Expected JSON, not a web page: {url}")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Download exceeded size limit")
        return data, response.headers.get("Content-Encoding", "").lower().strip()


def sync(root: Path, origin: str) -> dict:
    parsed = urlparse(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Origin must be an HTTPS origin without a path or credentials")
    origin = origin.rstrip("/")
    pointer, encoding = download(origin + "/data/current.json", MAX_MANIFEST_BYTES)
    pointer = decoded_bytes(pointer, encoding, MAX_MANIFEST_BYTES)
    manifest = decode_json(pointer)
    validate_manifest(manifest)
    root.mkdir(parents=True, exist_ok=True)
    releases = inside(root, root / "releases")
    releases.mkdir(exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".download-", dir=releases))
    temporary_pointer = None
    try:
        def fetch(entry: tuple[str, dict]) -> None:
            name, info = entry
            data, content_encoding = download(origin + manifest["base_path"] + "/" + name, compressed_limit(info))
            raw = decoded_bytes(data, content_encoding, info["bytes"])
            verify_object(raw, info, name)
            target = staged / (name + ".gz")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data if content_encoding == "gzip" else gzip.compress(raw, mtime=0))
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(fetch, manifest["objects"].items()))
        (staged / "manifest.json").write_bytes(pointer)
        destination = inside(root, releases / manifest["release_id"])
        if not destination.exists():
            try:
                staged.rename(destination)
            except OSError:
                # Another sync may have published the same immutable release
                # after our existence check. Verify its complete bytes below.
                if not destination.is_dir():
                    raise
        verify_release(root, manifest)
        # All objects have passed; failure above leaves the old pointer intact.
        # A unique temporary file prevents concurrent syncs sharing this write.
        with tempfile.NamedTemporaryFile(prefix=".current-", suffix=".json.tmp", dir=root, delete=False) as stream:
            temporary_pointer = Path(stream.name)
            stream.write(pointer)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_pointer, root / "current.json")
    finally:
        if temporary_pointer is not None and temporary_pointer.exists():
            temporary_pointer.unlink()
        if staged.exists():
            shutil.rmtree(staged)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "site/data")
    parser.add_argument("--origin", default="https://wattdocket.com")
    parser.add_argument("--verify-only", action="store_true", help="Verify local files without network access")
    args = parser.parse_args()
    manifest = verify_snapshot(args.root) if args.verify_only else sync(args.root, args.origin)
    print(json.dumps({"status": "verified", "release_id": manifest["release_id"], "built_at": manifest["built_at"], "objects": len(manifest["objects"]), "bytes": sum(x["bytes"] for x in manifest["objects"].values())}, indent=2))


if __name__ == "__main__":
    main()
