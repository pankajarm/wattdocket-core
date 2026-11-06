#!/usr/bin/env python3
"""Serve release-pinned Muse explanations on loopback without AWS.

Run from the repository root:
    python3 scripts/serve_local_explain.py

The default is the production handler's labeled source-evidence fallback. To
make paid calls using your own Meta key, set META_API_KEY, unset MODEL_OFF (or
set it to 0), and pass --enable-model. The browser needs
VITE_EXPLAIN_URL=http://127.0.0.1:8787/explain in web/.env.local.

This development adapter reuses the production identity, prompt and response
validation. Only storage and quota admission are replaced with local adapters.
Do not expose this server on a public interface.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
HANDLER_DIR = ROOT / "api" / "lambda_explain"
sys.path.insert(0, str(HANDLER_DIR))
from registry_loader import (  # noqa: E402
    MAX_COMPRESSED_BYTES, MAX_MANIFEST_BYTES, MAX_REGISTRY_BYTES,
    RegistryError, _manifest, decode, release_identity, validate_registry,
)

DEFAULT_ORIGINS = frozenset(
    f"http://{host}:{port}" for host in ("localhost", "127.0.0.1")
    for port in (5173, 4173)
)


def _read_file(path: Path, limit: int, compressed_limit: int | None = None) -> bytes:
    """Read bounded local bytes; hashes always cover uncompressed JSON."""
    try:
        with path.open("rb") as stream:
            raw = stream.read((compressed_limit or limit) + 1)
        if len(raw) > (compressed_limit or limit):
            raise RegistryError("REGISTRY_TOO_LARGE")
        if path.suffix == ".gz":
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise RegistryError("REGISTRY_TOO_LARGE")
        return raw
    except RegistryError:
        raise
    except (OSError, EOFError) as exc:
        raise RegistryError("REGISTRY_UNAVAILABLE") from exc


def _available(path: Path) -> Path:
    """Prefer the compressed snapshot when present; never reach the network."""
    compressed = path.with_suffix(path.suffix + ".gz")
    return compressed if compressed.is_file() else path


class LocalRegistry:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self._cache = None
        self._lock = threading.Lock()

    def __call__(self, release_id=None):
        if release_id is None:
            current = _manifest(_read_file(self.data_dir / "current.json", MAX_MANIFEST_BYTES))
            release_id = current["release_id"]
        if not release_identity(release_id):
            raise RegistryError("INVALID_RELEASE")
        release_dir = self.data_dir / "releases" / release_id
        # The ID grammar forbids traversal; this also prevents symlink escapes.
        if not release_dir.resolve().is_relative_to(self.data_dir):
            raise RegistryError("INVALID_RELEASE")
        manifest_path = _available(release_dir / "manifest.json")
        if not manifest_path.resolve().is_relative_to(self.data_dir):
            raise RegistryError("INVALID_RELEASE")
        manifest = _manifest(_read_file(manifest_path, MAX_MANIFEST_BYTES), release_id)
        spec = manifest["objects"]["explain-registry.json"]
        registry_path = _available(release_dir / "explain-registry.json")
        if not registry_path.resolve().is_relative_to(self.data_dir):
            raise RegistryError("INVALID_RELEASE")
        with self._lock:
            # Read and hash on every request: a changed local file cannot reuse
            # a previously trusted in-memory object. Only JSON parsing is cached.
            raw = _read_file(registry_path, MAX_REGISTRY_BYTES,
                             MAX_COMPRESSED_BYTES if registry_path.suffix == ".gz" else None)
            digest = hashlib.sha256(raw).hexdigest()
            if len(raw) != spec["bytes"] or digest != spec["sha256"]:
                raise RegistryError("REGISTRY_HASH_MISMATCH")
            cache_key = (release_id, digest, manifest["built_at"])
            if self._cache and self._cache[0] == cache_key:
                return self._cache[1]
            registry = validate_registry(decode(raw), manifest)
            self._cache = (cache_key, registry)
            return registry


class DailyQuota:
    """Atomic per-process UTC-day admission; restarting resets the local count."""
    def __init__(self, limit: int, clock=None):
        if not 1 <= limit <= 100:
            raise ValueError("daily limit must be between 1 and 100")
        self.limit = limit
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._day = None
        self._attempts = 0
        self._lock = threading.Lock()

    def __call__(self):
        now = self.clock()
        if now.tzinfo is None:
            return "QUOTA_UNAVAILABLE"
        day = now.astimezone(timezone.utc).date()
        with self._lock:
            if self._day != day:
                self._day, self._attempts = day, 0
            if self._attempts >= self.limit:
                return "DAILY_QUOTA_EXHAUSTED"
            self._attempts += 1
            return "ALLOWED"


def create_handler(data_dir: Path, daily_limit: int = 10):
    """Load a separate handler instance so tests/other adapters keep theirs."""
    spec = importlib.util.spec_from_file_location("_wattdocket_local_handler", HANDLER_DIR / "handler.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Production explain handler is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.load_registry = LocalRegistry(data_dir)
    module.claim_daily_attempt = DailyQuota(daily_limit)
    return module


def configure_model(enable: bool) -> bool:
    """Explicit opt-in is required even if a developer already has a key."""
    if not enable:
        os.environ["MODEL_OFF"] = "1"
        return False
    if os.environ.get("MODEL_OFF", "0").lower() in ("1", "true", "yes"):
        raise ValueError("MODEL_OFF disables calls; unset it or set MODEL_OFF=0 to opt in")
    if not os.environ.get("META_API_KEY", "").strip():
        raise ValueError("--enable-model requires your META_API_KEY")
    os.environ["MODEL_OFF"] = "0"
    return True


def valid_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")
                and parsed.port is not None and not parsed.username and not parsed.password
                and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def make_server(port: int, engine, allowed_origins=DEFAULT_ORIGINS):
    origins = frozenset(allowed_origins)
    if not all(valid_origin(origin) for origin in origins):
        raise ValueError("Only explicit HTTP loopback origins are allowed")

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "WattDocketLocal/1"

        def log_message(self, fmt, *args):
            # Do not log request bodies, keys, or upstream responses.
            return

        def reply(self, status, body=b"", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            origin = self.headers.get("Origin")
            if origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def allowed(self):
            expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in expected:
                self.reply(403, b'{"error":"Loopback Host required"}')
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin not in origins:
                self.reply(403, b'{"error":"Origin not allowed"}')
                return False
            if self.path != "/explain":
                self.reply(404, b'{"error":"Endpoint not found"}')
                return False
            return True

        def do_OPTIONS(self):
            if not self.allowed():
                return
            self.reply(204, extra={"Access-Control-Allow-Methods": "POST, OPTIONS",
                                   "Access-Control-Allow-Headers": "Content-Type"})

        def do_GET(self):
            if self.allowed():
                self.reply(405, b'{"error":"POST only"}', {"Allow": "POST, OPTIONS"})

        def do_POST(self):
            if not self.allowed():
                return
            if self.headers.get("Transfer-Encoding"):
                self.reply(400, b'{"error":"Transfer encoding is not supported"}')
                return
            if self.headers.get_content_type() != "application/json":
                self.reply(415, b'{"error":"Content-Type must be application/json"}')
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                length = -1
            if not 0 <= length <= engine.MAX_BODY:
                self.reply(413, b'{"error":"Invalid request length"}')
                return
            self.connection.settimeout(5)
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete body")
                body = raw.decode("utf-8")
            except (OSError, UnicodeError, ValueError):
                self.reply(400, b'{"error":"Invalid request body"}')
                return
            result = engine.handler({"httpMethod": "POST", "body": body}, None)
            self.reply(result["statusCode"], result["body"].encode("utf-8"))

    server = ThreadingHTTPServer(("127.0.0.1", port), RequestHandler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "site" / "data")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--daily-limit", type=int, default=10,
                        help="Paid attempts per UTC day per process, 1–100; restart resets the count")
    parser.add_argument("--enable-model", action="store_true", help="Opt into paid calls using your META_API_KEY")
    parser.add_argument("--allow-origin", action="append", default=[], help="Additional explicit HTTP loopback origin")
    args = parser.parse_args()
    try:
        if not 1 <= args.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        enabled = configure_model(args.enable_model)
        engine = create_handler(args.data_dir, args.daily_limit)
        engine.load_registry()  # Fail at startup if the snapshot is missing/corrupt.
        server = make_server(args.port, engine, DEFAULT_ORIGINS | set(args.allow_origin))
    except (ValueError, RegistryError, OSError) as exc:
        parser.error(str(exc))
    print(f"Local explain: http://127.0.0.1:{args.port}/explain", flush=True)
    print(f"Model calls: {'enabled; maximum ' + str(args.daily_limit) + ' attempts per UTC day per process' if enabled else 'disabled (source evidence summaries)'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
