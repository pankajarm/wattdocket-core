"""One network door for the ingestion lane — and one failure rule.

Why this module exists: the ledger is an evidence artifact, so a source that
did not answer must never look like a source with no rows. Every fetch either
returns bytes or raises :class:`IngestionError` carrying the exact URL that
failed. There is no empty-result default anywhere in this package.

Offline runs go through :class:`FixtureFetcher`, which resolves URLs against a
``manifest.json`` of captured responses. The fixtures are byte-for-byte what
the publisher returned (see ``capture_fixtures.py``), so an offline build is a
replay, not a re-enactment.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

USER_AGENT = "WattDocket/0.1 (data ingestion; +https://github.com/pankajarm/wattdocket-core)"
DEFAULT_TIMEOUT_SECONDS = 90


class IngestionError(RuntimeError):
    """A source failed to answer. Always carries the URL."""

    def __init__(self, message: str, url: str | None = None) -> None:
        self.url = url
        super().__init__(f"{message} [url={url}]" if url else message)


class Fetcher(Protocol):
    """Anything that can hand back the bytes of a URL."""

    def get_bytes(self, url: str) -> bytes: ...

    def get_json(self, url: str) -> Any: ...

    def describe(self) -> str: ...


class UrlFetcher:
    """Live HTTP. Keyless publishers only; no credentials touch this class."""

    def __init__(self, user_agent: str = USER_AGENT,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def get_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "*/*",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise IngestionError(f"HTTP {status} from source", url)
                return response.read()
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise IngestionError(f"HTTP {exc.code} from source", url) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise IngestionError(f"transport error: {exc.reason}", url) from exc
        except TimeoutError as exc:  # pragma: no cover - network path
            raise IngestionError("timed out", url) from exc

    def get_json(self, url: str) -> Any:
        raw = self.get_bytes(url)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IngestionError(f"response is not JSON: {exc}", url) from exc

    def describe(self) -> str:
        return f"live HTTP (UA={self.user_agent})"


class FixtureFetcher:
    """Replay captured responses from a directory of fixtures.

    ``manifest.json`` shape::

        {"entries": [{"url": "<exact url>", "file": "x.json"},
                     {"urls_file": "geo.json"},          # {"<url>": <body>, ...}
                     {"match": "fuel-mix.json", "file": "ercot.json"}]}

    Exact URLs win over ``urls_file`` maps, which win over substring matches;
    the first matching entry in manifest order wins within each class, so the
    mapping is deterministic. Extra keys (``sha256``, ``note``) are recorded for
    provenance and otherwise ignored.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        manifest_path = self.directory / "manifest.json"
        if not manifest_path.is_file():
            raise IngestionError(
                f"fixture manifest missing: {manifest_path} "
                f"(expected JSON with an 'entries' list)"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.exact: dict[str, str] = {}
        self.url_maps: list[dict] = []
        self.prefixes: list[tuple[str, str]] = []
        for entry in manifest.get("entries", []):
            if entry.get("urls_file"):
                path = self.directory / entry["urls_file"]
                if not path.is_file():
                    raise IngestionError(f"fixture url map missing: {path}")
                self.url_maps.append(json.loads(path.read_text(encoding="utf-8")))
                continue
            target = entry.get("file")
            if not target:
                raise IngestionError(f"fixture entry without 'file': {entry!r}")
            if entry.get("url"):
                self.exact[entry["url"]] = target
            elif entry.get("match"):
                self.prefixes.append((entry["match"], target))
            else:
                raise IngestionError(f"fixture entry without 'url' or 'match': {entry!r}")

    def _resolve(self, url: str) -> str:
        if url in self.exact:
            return self.exact[url]
        for needle, target in self.prefixes:
            if needle in url:
                return target
        raise IngestionError(
            "no captured fixture matches this URL; re-capture with "
            "python -m wattdocket.ingest.capture_fixtures",
            url,
        )

    def get_bytes(self, url: str) -> bytes:
        for url_map in self.url_maps:
            if url in url_map:
                body = url_map[url]
                text = body if isinstance(body, str) else json.dumps(body, sort_keys=True)
                return text.encode("utf-8")
        path = self.directory / self._resolve(url)
        if not path.is_file():
            raise IngestionError(f"fixture file missing: {path}", url)
        return path.read_bytes()

    def get_json(self, url: str) -> Any:
        raw = self.get_bytes(url)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IngestionError(f"fixture is not JSON: {exc}", url) from exc

    def describe(self) -> str:
        return f"fixtures in {self.directory}"


def default_fetcher(fixture_dir: str | Path | None = None) -> Fetcher:
    """Fixtures when a directory is given, live HTTP otherwise."""
    return FixtureFetcher(fixture_dir) if fixture_dir else UrlFetcher()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: Any) -> str:
    """Stable JSON for hashing: sorted keys, no whitespace drift."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def url_with_params(base: str, params: dict[str, str]) -> str:
    """Deterministic URL builder (query order follows the dict, values encoded)."""
    return f"{base}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
