"""Bounded release-pinned facts loaded only from the configured S3 bucket."""
from collections import OrderedDict
import gzip
import hashlib
import io
import json
import os
import re
import threading
import time

VERSION = "release-registry-v1"
KINDS = {"facility", "disclosure_location", "power_site", "permit_document"}
MAX_REGISTRY_BYTES = 16_000_000
MAX_COMPRESSED_BYTES = 4_000_000
MAX_MANIFEST_BYTES = 500_000
CACHE_SECONDS = 300
CURRENT_SECONDS = 30
MAX_CACHED_RELEASES = 1
_LOCK = threading.RLock()
_RELEASES = OrderedDict()
_CURRENT = {}


class RegistryError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def identity(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,179}", value))


def release_identity(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value))


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RegistryError("INVALID_REGISTRY")
            result[key] = value
        return result
    def invalid(value):
        raise RegistryError("INVALID_REGISTRY")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise RegistryError("INVALID_REGISTRY") from exc


def _client():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", config=Config(connect_timeout=1, read_timeout=2,
                                          retries={"total_max_attempts": 1}))


def _read(client, bucket, key, limit):
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            if response.get("ContentLength", 0) > limit:
                raise RegistryError("REGISTRY_TOO_LARGE")
            raw = body.read(limit + 1)
        finally:
            body.close()
        if len(raw) > limit:
            raise RegistryError("REGISTRY_TOO_LARGE")
        return raw
    except RegistryError:
        raise
    except Exception as exc:
        raise RegistryError("REGISTRY_UNAVAILABLE") from exc


def _manifest(raw, expected=None):
    value = decode(raw)
    if not isinstance(value, dict) or not release_identity(value.get("release_id")):
        raise RegistryError("INVALID_MANIFEST")
    rid = value["release_id"]
    if expected is not None and rid != expected:
        raise RegistryError("RELEASE_MISMATCH")
    if value.get("base_path") != "/data/releases/" + rid:
        raise RegistryError("INVALID_MANIFEST")
    spec = (value.get("objects") or {}).get("explain-registry.json")
    if not isinstance(spec, dict):
        raise RegistryError("EXPLAIN_NOT_IN_RELEASE")
    if (not isinstance(spec.get("sha256"), str) or not re.fullmatch("[a-f0-9]{64}", spec["sha256"])
            or type(spec.get("bytes")) is not int or not 0 < spec["bytes"] <= MAX_REGISTRY_BYTES
            or type(spec.get("gzip_bytes")) is not int or not 0 < spec["gzip_bytes"] <= MAX_COMPRESSED_BYTES):
        raise RegistryError("INVALID_MANIFEST")
    return value


def validate_registry(registry, manifest):
    if (not isinstance(registry, dict) or registry.get("schema_version") != "1"
            or registry.get("release_id") != manifest["release_id"] or registry.get("built_at") != manifest.get("built_at")):
        raise RegistryError("RELEASE_MISMATCH")
    subjects = registry.get("subjects")
    if not isinstance(subjects, dict) or not 1 <= len(subjects) <= 10_000:
        raise RegistryError("INVALID_REGISTRY")
    aliases = registry.get("aliases", {})
    if not isinstance(aliases, dict) or len(aliases) > 10_000:
        raise RegistryError("INVALID_REGISTRY")
    for alias, target in aliases.items():
        if not identity(alias) or not isinstance(target, str) or target not in subjects or not target.startswith("facility:"):
            raise RegistryError("INVALID_REGISTRY")
    # Validate facts when selected; the complete file's length/hash and subject
    # index are checked before caching. Unrequested subjects cannot reach prompts.
    for key, subject in subjects.items():
        if (not isinstance(subject, dict) or subject.get("kind") not in KINDS or not identity(subject.get("id"))
                or key != subject["kind"] + ":" + subject["id"]):
            raise RegistryError("INVALID_REGISTRY")
    return registry


def load_registry(release_id=None, *, client=None, clock=time.monotonic):
    """A request can choose an immutable release ID, never a bucket or URL."""
    bucket = os.environ.get("WATTDOCKET_EXPLAIN_DATA_BUCKET", "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise RegistryError("REGISTRY_NOT_CONFIGURED")
    if release_id is not None and not release_identity(release_id):
        raise RegistryError("INVALID_RELEASE")
    now = clock()
    # Serializing the cold load avoids duplicate S3 work in concurrent in-process
    # callers. Lambda environments remain independent and bounded by their quota.
    with _LOCK:
        for key, entry in list(_RELEASES.items()):
            if entry["expires"] <= now:
                del _RELEASES[key]
        conn = client
        if release_id is None:
            cached = _CURRENT.get(bucket)
            if cached and cached["expires"] > now:
                release_id = cached["release_id"]
            else:
                conn = conn or _client()
                current = _manifest(_read(conn, bucket, "data/current.json", MAX_MANIFEST_BYTES))
                release_id = current["release_id"]
                _CURRENT.clear()
                _CURRENT[bucket] = {"release_id": release_id, "expires": now + CURRENT_SECONDS}
        key = (bucket, release_id, VERSION)
        cached = _RELEASES.get(key)
        if cached:
            _RELEASES.move_to_end(key)
            return cached["registry"]
        # Evict before parsing another complete registry: Lambda has a bounded
        # memory allocation and must not transiently hold two full JSON trees.
        while len(_RELEASES) >= MAX_CACHED_RELEASES:
            _RELEASES.popitem(last=False)
        conn = conn or _client()
        prefix = "data/releases/" + release_id + "/"
        manifest = _manifest(_read(conn, bucket, prefix + "manifest.json", MAX_MANIFEST_BYTES), release_id)
        spec = manifest["objects"]["explain-registry.json"]
        raw = _read(conn, bucket, prefix + "explain-registry.json", MAX_COMPRESSED_BYTES)
        try:
            if raw.startswith(b"\x1f\x8b"):
                if len(raw) != spec["gzip_bytes"]:
                    raise RegistryError("REGISTRY_HASH_MISMATCH")
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                    data = compressed.read(MAX_REGISTRY_BYTES + 1)
            else:
                data = raw
        except (OSError, EOFError) as exc:
            raise RegistryError("INVALID_REGISTRY") from exc
        if len(data) != spec["bytes"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise RegistryError("REGISTRY_HASH_MISMATCH")
        registry = validate_registry(decode(data), manifest)
        _RELEASES[key] = {"registry": registry, "sha256": spec["sha256"], "expires": now + CACHE_SECONDS}
        while len(_RELEASES) > MAX_CACHED_RELEASES:
            _RELEASES.popitem(last=False)
        return registry


def resolve_subject(registry, kind, identifier, *, legacy=False):
    key = kind + ":" + identifier
    if legacy:
        key = registry.get("aliases", {}).get(identifier, key)
    subject = registry["subjects"].get(key)
    if subject is None:
        raise RegistryError("UNKNOWN_SUBJECT")
    return subject


def clear_cache():
    """Explicit test hook; cache expiration is enforced independently on reads."""
    with _LOCK:
        _CURRENT.clear()
        _RELEASES.clear()
