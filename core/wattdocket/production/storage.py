"""Small object-store boundary shared by local replay and AWS jobs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ConflictError(RuntimeError):
    pass


class LocalStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("Object key escapes store")
        return path

    def get(self, key: str) -> tuple[bytes, str] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        raw = path.read_bytes()
        return raw, digest(raw)

    def put(self, key: str, data: bytes, *, expected: str | None = None,
            create_only: bool = False, content_type="application/json",
            cache_control="no-cache", content_encoding: str | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # File lock covers compare and replace, including concurrent publishers.
        import fcntl
        with (self.root / ".write.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            old = self.get(key)
            if create_only and old is not None:
                if old[0] == data:
                    return old[1]
                raise ConflictError(f"Object already exists: {key}")
            if expected is not None and (old is None or old[1] != expected):
                raise ConflictError(f"Object changed: {key}")
            temp = path.with_name(path.name + ".tmp")
            temp.write_bytes(data)
            temp.replace(path)
        return digest(data)


class S3Store:
    def __init__(self, bucket: str):
        import boto3
        self.bucket = bucket
        self.client = boto3.client("s3")

    def get(self, key: str) -> tuple[bytes, str] | None:
        from botocore.exceptions import ClientError
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
            return obj["Body"].read(), obj["ETag"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound"):
                return None
            raise

    def put(self, key: str, data: bytes, *, expected: str | None = None,
            create_only: bool = False, content_type="application/json",
            cache_control="no-cache", content_encoding: str | None = None) -> str:
        from botocore.exceptions import ClientError
        args = dict(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                    CacheControl=cache_control, Metadata={"sha256": digest(data)})
        if expected is not None:
            args["IfMatch"] = expected
        if create_only:
            args["IfNoneMatch"] = "*"
        if content_encoding:
            args["ContentEncoding"] = content_encoding
        try:
            response = self.client.put_object(**args)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("PreconditionFailed", "ConditionalRequestConflict", "412", "409"):
                if create_only:
                    prior = self.get(key)
                    if prior and prior[0] == data:
                        return prior[1]
                raise ConflictError(f"Concurrent object change: {key}") from exc
            raise
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        if head.get("Metadata", {}).get("sha256") != digest(data) or head["ContentLength"] != len(data):
            raise RuntimeError(f"Uploaded object failed digest/length verification: {key}")
        return response["ETag"]


def read_json(store, key: str, default=None):
    obj = store.get(key)
    return json.loads(obj[0]) if obj else default
