"""Retrying, bounded HTTPS capture with immutable receipts and document caching."""
from __future__ import annotations

import gzip
import json
import math
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .storage import ConflictError, digest, json_bytes, read_json


def stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parsed(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FetchBudgetExceeded(RuntimeError):
    """The source's shared upstream deadline was exhausted."""


class CaptureFetcher:
    def __init__(self, store, now: datetime, *, timeout=45, attempts=3,
                 max_bytes=200_000_000, document_max_age=30 * 86400,
                 source_budget_seconds=150):
        if not math.isfinite(source_budget_seconds) or source_budget_seconds <= 0:
            raise ValueError("Source fetch budget must be finite and positive")
        self.store, self.now = store, now
        self.timeout, self.attempts, self.max_bytes = timeout, attempts, max_bytes
        self.document_max_age = document_max_age
        self._deadline = time.monotonic() + source_budget_seconds
        self.receipts: dict[str, dict] = {}
        self._reader_lock = threading.Lock()
        self._reader_next = 0.0
        self._reader_blocked_until = 0.0
        try:
            import certifi
            self.context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self.context = ssl.create_default_context()

    def _remaining(self):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise FetchBudgetExceeded("Source upstream wall-clock budget exhausted")
        return remaining

    def _sleep(self, delay):
        remaining = self._remaining()
        if delay >= remaining:
            raise FetchBudgetExceeded("Source upstream budget cannot accommodate another retry or reader delay")
        time.sleep(delay)

    def _read_response(self, response, *, timeout):
        """Check the shared deadline between single socket reads, not per URL."""
        chunks, length = [], 0
        read = getattr(response, "read1", response.read)
        while True:
            remaining = self._remaining()
            # urllib's HTTPResponse exposes its buffered socket here. Updating
            # its timeout before read1 bounds a stall late in a long response
            # by the remaining source time, rather than the original timeout.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(min(timeout, remaining))
            chunk = read(min(1024 * 1024, self.max_bytes + 1 - length))
            self._remaining()
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            length += len(chunk)
            if length > self.max_bytes:
                raise ValueError("Publisher response exceeds the bounded capture size")

    def get_bytes(self, url: str) -> bytes:
        return self._request_bytes(url)

    def get_publisher_pdf(self, url: str) -> bytes:
        """Anonymous browser-compatible retrieval for DEQ's public PDFs.

        The publisher rejects our identified research UA, including a Mozilla
        compatible variant, but serves these same public URLs to Mozilla/5.0.
        This profile adds no cookies, credentials, proxy or paid service.
        """
        parts = urllib.parse.urlparse(url)
        if (parts.scheme != "https" or parts.hostname not in {"www.deq.virginia.gov", "deq.virginia.gov"}
                or not parts.path.lower().startswith("/home/showpublisheddocument/")):
            raise ValueError("Public PDF request profile is restricted to Virginia DEQ document URLs")
        return self._request_bytes(url, request_profile="deq_public_pdf")

    def post_json(self, url: str, payload):
        """Capture a public read-only search whose publisher requires POST.

        Request bodies participate in the cache identity and receipt. Adapters
        may use this only for documented public query endpoints, not mutations.
        """
        body = json_bytes(payload)
        if len(body) > 64_000:
            raise ValueError("Public query request exceeds size limit")
        return json.loads(self._request_bytes(url, body=body))

    def _request_bytes(self, url: str, *, body: bytes | None = None,
                       request_profile: str | None = None) -> bytes:
        parts = urllib.parse.urlparse(url)
        if parts.scheme != "https" or parts.username or parts.password:
            raise ValueError("Only credential-free HTTPS publisher URLs are accepted")
        request_hash = digest(body) if body is not None else None
        key = "http/" + digest(url.encode() + (b"\nPOST\n" + body if body is not None else b"")) + ".json"
        receipt_key = url if body is None else url + "#request=" + request_hash
        prior = read_json(self.store, key)
        # Registry/API metadata is checked at every source refresh; immutable
        # linked permits are cached without claiming a new retrieval timestamp.
        is_document = any(token in parts.path.lower() for token in
                          (".pdf", ".xlsx", "/showpublisheddocument/"))
        is_document = is_document or "TCEQ_EXTERNAL_SEARCH_GET_FILE" in parts.query
        if body is None and prior and is_document and (self.now - parsed(prior["retrieved_at"])).total_seconds() < self.document_max_age:
            obj = self.store.get(prior["raw_key"])
            if obj:
                data = gzip.decompress(obj[0])
                if digest(data) != prior["sha256"]:
                    raise ValueError("Cached source digest mismatch")
                self.receipts[url] = {**prior, "cache_hit": True}
                return data
        headers = {"User-Agent": "WattDocket/2.0 (+https://wattdocket.com; public-record research)",
                   "Accept": "*/*", "Accept-Encoding": "identity"}
        if request_profile == "deq_public_pdf":
            headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/pdf"})
        if body is not None:
            headers["Content-Type"] = "application/json"
        if prior and body is None:
            if prior.get("etag"):
                headers["If-None-Match"] = prior["etag"]
            elif prior.get("last_modified"):
                headers["If-Modified-Since"] = prior["last_modified"]
        last_error = None
        overpass = parts.hostname in {"overpass-api.de", "overpass.kumi.systems"}
        # The OSM adapter already has a sequential mirror fallback. Avoid
        # repeating a 90-second query at one host and starving that fallback.
        attempt_limit = 1 if overpass else self.attempts
        for attempt in range(attempt_limit):
            try:
                if parts.hostname == "r.jina.ai":
                    # Stay below the anonymous reader's request allowance.
                    # Cached documents do not consume this limiter.
                    with self._reader_lock:
                        if time.monotonic() < self._reader_blocked_until:
                            raise RuntimeError("Reader rate limit: deferred until its Retry-After expires")
                        delay = max(0, self._reader_next - time.monotonic())
                        if delay:
                            self._sleep(delay)
                        self._reader_next = time.monotonic() + 4.1
                req = urllib.request.Request(url, headers=headers, data=body)
                timeout = max(self.timeout, 150) if parts.hostname == "services.arcgis.com" else self.timeout
                if overpass:
                    timeout = max(timeout, 110)
                timeout = min(timeout, self._remaining())
                with urllib.request.urlopen(req, timeout=timeout, context=self.context) as response:
                    size = response.headers.get("Content-Length")
                    if size and int(size) > self.max_bytes:
                        raise ValueError("Publisher response exceeds the bounded capture size")
                    raw = self._read_response(response, timeout=timeout)
                    if not raw:
                        raise ValueError("Publisher returned an empty response")
                    sha = digest(raw)
                    raw_key = f"raw/{sha}.gz"
                    try:
                        self.store.put(raw_key, gzip.compress(raw, mtime=0), create_only=True,
                                       content_type="application/gzip")
                    except ConflictError:
                        # The key identifies publisher bytes, not gzip output.
                        # Python/zlib versions and OS headers can encode those
                        # identical bytes differently. Never replace the first
                        # archived representation; verify its logical content.
                        archived = self.store.get(raw_key)
                        if not archived or digest(gzip.decompress(archived[0])) != sha:
                            raise ValueError("Existing raw evidence does not match its content-addressed key")
                    receipt = {"url": url, "final_url": response.url,
                               "retrieved_at": stamp(self.now), "sha256": sha,
                               "raw_key": raw_key, "bytes": len(raw),
                               "content_type": response.headers.get_content_type(),
                               "etag": response.headers.get("ETag"),
                               "last_modified": response.headers.get("Last-Modified")}
                    if request_profile:
                        receipt.update(request_profile=request_profile,
                                       request_headers={k: headers[k] for k in ("User-Agent", "Accept", "Accept-Encoding")})
                    if body is not None:
                        receipt.update(request_method="POST", request_body_sha256=request_hash,
                                       request_body=json.loads(body))
                self.store.put(key, json_bytes(receipt))
                self.receipts[receipt_key] = receipt
                return raw
            except urllib.error.HTTPError as exc:
                if exc.code == 304 and body is not None:
                    raise ValueError("Public POST query returned an unsolicited 304 response") from exc
                if exc.code == 304 and prior:
                    obj = self.store.get(prior["raw_key"])
                    if obj:
                        raw = gzip.decompress(obj[0])
                        if digest(raw) != prior["sha256"]:
                            raise ValueError("Revalidated source digest mismatch")
                        receipt = {**prior, "checked_at": stamp(self.now), "http_status": 304}
                        self.receipts[url] = receipt
                        return raw
                    # A lifecycle rule or lost object can leave HTTP metadata
                    # behind after its body expires. A 304 cannot restore it;
                    # spend the next bounded attempt on an unconditional fetch.
                    headers.pop("If-None-Match", None)
                    headers.pop("If-Modified-Since", None)
                    last_error = f"HTTP 304 without retained source body at {url}"
                    continue
                last_error = f"HTTP {exc.code} at {url}"
                if exc.code == 429:
                    # Honor Retry-After without holding the build in a long
                    # sleep. Subsequent requests fail closed during cooldown.
                    retry = exc.headers.get("Retry-After", "60")
                    try:
                        seconds = float(retry)
                    except ValueError:
                        try:
                            seconds = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                        except (TypeError, ValueError):
                            seconds = 60
                    if parts.hostname == "r.jina.ai":
                        with self._reader_lock:
                            self._reader_blocked_until = time.monotonic() + max(seconds, 1)
                    break
                if exc.code not in (408, 429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__} at {url}: {str(exc)[:200]}"
            if attempt + 1 < attempt_limit:
                self._sleep(min(2 ** attempt, 4))
        raise RuntimeError(last_error or f"Source fetch failed: {url}")

    def get_json(self, url: str):
        raw = self.get_bytes(url)
        if raw.lstrip().startswith((b"<", b"<!")):
            raise ValueError(f"Publisher returned HTML instead of JSON: {url}")
        return json.loads(raw)

    def receipt(self, url: str) -> dict | None:
        return self.receipts.get(url)
