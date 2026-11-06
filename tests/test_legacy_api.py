"""Retired legacy explanations cannot consume credentials or call a model."""
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import threading
import time
from http.client import HTTPConnection

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "api/wattdocket_api" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_retired_composer_never_reads_keys_or_calls_network(monkeypatch):
    import builtins
    import urllib.request
    composer = module("legacy_composer", "explain.py")
    monkeypatch.setenv("META_API_KEY", "unused-test-key")
    monkeypatch.setenv("MODEL_OFF", "0")
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: pytest.fail("Credential/file access"))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("Network call"))
    with pytest.raises(composer.ExplainUnavailable, match="LEGACY_EXPLAIN_RETIRED"):
        composer.compose({"facility_id": "untrusted", "text": "Invented 123456789 MW"})


def test_optional_legacy_http_service_is_keyless_and_explain_is_gone(monkeypatch, tmp_path):
    pytest.importorskip("fastapi", reason="Optional legacy API test requires core[api]")
    uvicorn = pytest.importorskip("uvicorn", reason="Optional legacy API test requires core[api]")
    db = tmp_path / "public-fixture.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("create table facility (id text, name text)")
        connection.execute("insert into facility values (?, ?)", ("test-record", "Public fixture"))
    monkeypatch.setenv("WATTDOCKET_LEDGER", str(db))
    monkeypatch.setenv("WATTDOCKET_ARTIFACTS", str(tmp_path))
    monkeypatch.setenv("MODEL_OFF", "0")  # Even explicit old opt-in cannot resurrect this route.
    monkeypatch.delenv("META_API_KEY", raising=False)
    app = module("legacy_api_http", "main.py")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app.app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        def fetch(method, path, body=None):
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        status, health = fetch("GET", "/healthz")
        assert status == 200 and health["ledger"] == "ledger" and health["model_enabled"] is False
        status, facilities = fetch("GET", "/v1/facilities")
        assert status == 200 and facilities["results"] == [{"id": "test-record", "name": "Public fixture"}]
        status, result = fetch("POST", "/v1/explain", json.dumps({"payload": {"number": 123456789}, "template": "attacker text"}))
        assert status == 410 and result["detail"]["reason"] == "LEGACY_EXPLAIN_RETIRED"
        assert "attacker text" not in json.dumps(result) and "123456789" not in json.dumps(result)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()
