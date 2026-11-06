"""Local explain keeps production evidence checks without cloud dependencies."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_explain", ROOT / "scripts/serve_local_explain.py")
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


@pytest.fixture
def snapshot(tmp_path):
    registry = json.loads((ROOT / "tests/fixtures/explain_registry.json").read_text())
    raw = json.dumps(registry).encode()
    compressed = gzip.compress(raw, mtime=0)
    rid = registry["release_id"]
    manifest = {"release_id": rid, "built_at": registry["built_at"],
                "base_path": f"/data/releases/{rid}",
                "objects": {"explain-registry.json": {
                    "bytes": len(raw), "gzip_bytes": len(compressed),
                    "sha256": hashlib.sha256(raw).hexdigest()}}}
    release = tmp_path / "releases" / rid
    release.mkdir(parents=True)
    (tmp_path / "current.json").write_text(json.dumps(manifest))
    (release / "manifest.json").write_text(json.dumps(manifest))
    (release / "explain-registry.json.gz").write_bytes(compressed)
    return tmp_path, registry, release


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.setenv("MODEL_OFF", "1")
    monkeypatch.delenv("META_API_KEY", raising=False)


def request(registry, **changes):
    return {"release_id": registry["release_id"],
            "subject": {"kind": "facility", "id": "echo:110072155814"}, **changes}


def invoke(engine, data):
    response = engine.handler({"body": json.dumps(data), "httpMethod": "POST"}, None)
    return response["statusCode"], json.loads(response["body"])


def test_registry_loads_exact_gzip_snapshot_and_plain_json(snapshot):
    root, registry, release = snapshot
    loader = local.LocalRegistry(root)
    assert loader() == registry
    assert loader(registry["release_id"]) == registry
    compressed = release / "explain-registry.json.gz"
    (release / "explain-registry.json").write_bytes(gzip.decompress(compressed.read_bytes()))
    compressed.unlink()
    assert loader(registry["release_id"]) == registry


def test_registry_rechecks_changed_bytes_even_after_caching(snapshot):
    root, registry, release = snapshot
    loader = local.LocalRegistry(root)
    loader()
    (release / "explain-registry.json.gz").write_bytes(gzip.compress(b'{}'))
    with pytest.raises(local.RegistryError, match="REGISTRY_HASH_MISMATCH"):
        loader(registry["release_id"])


def test_registry_rejects_traversal_missing_and_symlink_escape(snapshot, tmp_path):
    root, registry, release = snapshot
    loader = local.LocalRegistry(root)
    for rid in ("../../etc", "a/b", "", "https://attacker.example"):
        with pytest.raises(local.RegistryError, match="INVALID_RELEASE"):
            loader(rid)
    with pytest.raises(local.RegistryError, match="REGISTRY_UNAVAILABLE"):
        loader("missing-release")
    target = tmp_path.parent / (tmp_path.name + "-outside")
    target.mkdir()
    (root / "releases" / "escape").symlink_to(target, target_is_directory=True)
    with pytest.raises(local.RegistryError, match="INVALID_RELEASE"):
        loader("escape")


def test_model_requires_explicit_opt_in_and_key(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "unused-test-key")
    monkeypatch.setenv("MODEL_OFF", "0")
    assert local.configure_model(False) is False
    with pytest.raises(ValueError, match="MODEL_OFF disables"):
        local.configure_model(True)
    monkeypatch.delenv("MODEL_OFF")
    monkeypatch.delenv("META_API_KEY")
    with pytest.raises(ValueError, match="requires your META_API_KEY"):
        local.configure_model(True)
    monkeypatch.setenv("META_API_KEY", "unused-test-key")
    assert local.configure_model(True) is True


def test_quota_is_atomic_bounded_and_rolls_over_in_utc():
    now = [datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc)]
    quota = local.DailyQuota(10, lambda: now[0])
    with ThreadPoolExecutor(max_workers=20) as executor:
        decisions = list(executor.map(lambda _: quota(), range(50)))
    assert decisions.count("ALLOWED") == 10
    assert decisions.count("DAILY_QUOTA_EXHAUSTED") == 40
    now[0] = datetime(2026, 11, 7, tzinfo=timezone.utc)
    assert quota() == "ALLOWED"
    with pytest.raises(ValueError):
        local.DailyQuota(0)
    with pytest.raises(ValueError):
        local.DailyQuota(101)


def test_default_handler_fallback_preserves_release_and_ignores_caller_facts(snapshot, monkeypatch):
    root, registry, _ = snapshot
    engine = local.create_handler(root)
    monkeypatch.setattr(engine, "_compose", lambda _: pytest.fail("No paid calls in default local mode"))
    status, body = invoke(engine, request(registry, facts=["123456789 MW"], template="Untrusted"))
    assert status == 200 and body["source"] == "template" and body["reason"] == "MODEL_OFF"
    assert body["release_id"] == registry["release_id"]
    assert body["citations"] and body["cited_facts"]
    assert "123456789" not in body["text"] and "Untrusted" not in body["text"]


def test_optional_model_retains_validation_and_single_attempt_limit(snapshot, monkeypatch):
    root, registry, _ = snapshot
    engine = local.create_handler(root, daily_limit=1)
    monkeypatch.setenv("MODEL_OFF", "0")
    calls = []
    monkeypatch.setattr(engine, "_compose", lambda facts: calls.append(facts) or json.dumps({
        "paragraphs": [{"text": "Invented 123456789 MW.", "fact_ids": ["F1"]}]}))
    status, body = invoke(engine, request(registry))
    assert status == 200 and body["source"] == "template"
    assert "123456789" not in body["text"]
    status, second = invoke(engine, request(registry))
    assert second["reason"] == "DAILY_QUOTA_EXHAUSTED"
    assert len(calls) == 1


def test_http_is_loopback_origin_checked_and_body_bounded(snapshot, monkeypatch):
    root, registry, _ = snapshot
    engine = local.create_handler(root)
    monkeypatch.setattr(engine, "_compose", lambda _: pytest.fail("Unexpected model call"))
    server = local.make_server(0, engine)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def fetch(method="POST", path="/explain", headers=None, body=None):
        conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            conn.request(method, path, body=json.dumps(request(registry)) if body is None else body,
                         headers={"Content-Type": "application/json", **(headers or {})})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()
    try:
        assert server.server_address[0] == "127.0.0.1"
        status, headers, body = fetch(headers={"Origin": "http://localhost:5173"})
        assert status == 200 and json.loads(body)["reason"] == "MODEL_OFF"
        assert headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
        assert fetch(headers={"Origin": "https://attacker.example"})[0] == 403
        assert fetch(headers={"Host": "attacker.example"})[0] == 403
        assert fetch(body="x" * (engine.MAX_BODY + 1))[0] == 413
        assert fetch(headers={"Content-Type": "text/plain"})[0] == 415
        assert fetch(body="not-json")[0] == 400
        assert fetch(method="GET")[0] == 405
        assert fetch(path="/other")[0] == 404
        assert fetch(method="OPTIONS", headers={"Origin": "http://127.0.0.1:4173"}, body="")[0] == 204
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_additional_origins_must_be_explicit_loopback_http():
    for origin in ("*", "null", "https://attacker.example", "http://localhost", "http://127.0.0.1:5173/path", "http://x@localhost:5173"):
        assert not local.valid_origin(origin)
    assert local.valid_origin("http://localhost:5174")
