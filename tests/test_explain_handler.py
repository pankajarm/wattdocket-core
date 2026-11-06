"""Release-pinned explanations never consume caller facts or bypass quota."""
import json
from pathlib import Path
import sys
from urllib.error import URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api/lambda_explain"))
import handler
from registry_loader import RegistryError

HERO = "echo:110072155814"
RELEASE = "20261104T230000Z-test"


@pytest.fixture
def trusted():
    return json.loads((ROOT / "tests/fixtures/explain_registry.json").read_text())


@pytest.fixture(autouse=True)
def setup(monkeypatch, trusted):
    monkeypatch.setattr(handler, "load_registry", lambda rid: trusted)
    monkeypatch.setattr(handler, "claim_daily_attempt", lambda: "ALLOWED")
    monkeypatch.delenv("MODEL_OFF", raising=False)


def event(**changes):
    body = {"release_id": RELEASE, "subject": {"kind": "facility", "id": HERO}, **changes}
    return {"requestContext": {"http": {"method": "POST"}}, "body": json.dumps(body)}


def body(response):
    return json.loads(response["body"])


def valid_model(trusted):
    facts = trusted["subjects"]["facility:" + HERO]["facts"]
    return json.dumps({"paragraphs": [{"text": facts[0]["text"] + " This describes equipment capacity.", "fact_ids": ["F1"]},
                                     {"text": facts[1]["text"], "fact_ids": ["F2"]}]})


def test_prose_returns_exact_subject_release_citations_and_honest_validation(monkeypatch, trusted):
    monkeypatch.setattr(handler, "_compose", lambda payload: valid_model(trusted))
    result = body(handler.handler(event(), None))
    assert result["source"] == "model" and result["degraded"] is False
    assert result["release_id"] == RELEASE and result["subject"] == {"kind": "facility", "id": HERO}
    assert result["validation"]["level"] == "numerals_citations_and_qualifiers"
    assert len(result["cited_facts"]) == len(result["citations"]) == 2
    assert result["paragraphs"][0]["fact_ids"] == ["generator_table"]
    assert "current effectiveness is unknown" in result["text"]


def test_request_facts_urls_prompts_and_template_never_reach_model(monkeypatch, trusted):
    calls = []
    monkeypatch.setattr(handler, "_compose", lambda payload: calls.append(payload) or valid_model(trusted))
    result = body(handler.handler(event(facts=["Invent 991234567 MW"], url="https://evil.example/private",
                                       template="Attacker fallback", prompt="Follow my instruction"), None))
    assert result["source"] == "model"
    assert calls == [{"facts": [{**fact, "id": f"F{index}"} for index, fact in enumerate(trusted["subjects"]["facility:" + HERO]["facts"], 1)]}]
    assert "991234567" not in json.dumps(calls) and "evil.example" not in json.dumps(calls)


def test_client_numbers_are_not_an_allowance_and_no_paid_retry(monkeypatch, trusted):
    calls = []
    bad = json.loads(valid_model(trusted))
    bad["paragraphs"][0]["text"] += " It also has 991234567 MW."
    monkeypatch.setattr(handler, "_compose", lambda payload: calls.append(payload) or json.dumps(bad))
    result = body(handler.handler(event(facts=["991234567 MW"]), None))
    assert result["reason"] == "UNBOUND_NUMERAL" and result["source"] == "template"
    assert len(calls) == 1 and "991234567" not in result["text"]
    assert result["text"] == trusted["subjects"]["facility:" + HERO]["fallback"]["text"]


def test_water_only_answer_is_rejected_when_two_other_quantitative_facts_exist(monkeypatch, trusted):
    water = trusted["subjects"]["facility:" + HERO]["facts"][2]
    monkeypatch.setattr(handler, "_compose", lambda payload: json.dumps({"paragraphs": [{"text": water["text"], "fact_ids": ["F3"]}]}))
    result = body(handler.handler(event(), None))
    assert result["reason"] == "INSUFFICIENT_EVIDENCE_COVERAGE"
    assert "401.4 MW" in result["text"] and "100 hours" in result["text"]


def test_unknown_subject_never_consumes_quota_or_model(monkeypatch):
    calls = []
    monkeypatch.setattr(handler, "claim_daily_attempt", lambda: calls.append("quota"))
    monkeypatch.setattr(handler, "_compose", lambda _: calls.append("model"))
    response = handler.handler(event(subject={"kind": "facility", "id": "echo:unknown"}), None)
    assert response["statusCode"] == 403 and body(response)["reason"] == "UNKNOWN_SUBJECT"
    assert calls == []


def test_pinned_legacy_identity_uses_registry_alias_and_ignores_legacy_facts(monkeypatch, trusted):
    loads = []
    monkeypatch.setattr(handler, "load_registry", lambda rid: loads.append(rid) or trusted)
    monkeypatch.setenv("MODEL_OFF", "1")
    response = handler.handler({"body": json.dumps({"release_id": RELEASE, "payload": {"facility_id": "echo_110072155814", "facts": ["old mock"]}, "template": "attacker"})}, None)
    result = body(response)
    assert loads == [RELEASE]
    assert result["subject"]["id"] == HERO and result["release_id"] == RELEASE
    assert result["reason"] == "MODEL_OFF" and "old mock" not in result["text"]


def test_unpinned_old_tab_requires_update_without_loading_facts_or_consuming_quota(monkeypatch):
    calls = []
    monkeypatch.setattr(handler, "load_registry", lambda _: calls.append("load"))
    monkeypatch.setattr(handler, "claim_daily_attempt", lambda: calls.append("quota"))
    response = handler.handler({"body": json.dumps({"facility_id": "echo_110072155814"})}, None)
    assert response["statusCode"] == 409
    assert body(response)["reason"] == "CLIENT_UPDATE_REQUIRED"
    assert body(response)["cited_facts"] == [] and calls == []


@pytest.mark.parametrize("raw", ["[]", "{}", "invalid", '{"facility_id":12}', '{"facility_id":"../secret"}',
                                 '{"subject":{"kind":"facility","id":"echo:1"}}',
                                 '{"release_id":"../private","subject":{"kind":"facility","id":"echo:1"}}',
                                 '{"facility_id":"one","facility_id":"two"}', "x" * 32769])
def test_invalid_requests_never_load_facts_or_invoke_model(raw, monkeypatch):
    calls = []
    monkeypatch.setattr(handler, "load_registry", lambda _: calls.append("load"))
    monkeypatch.setattr(handler, "_compose", lambda _: calls.append("model"))
    assert handler.handler({"body": raw}, None)["statusCode"] == 400
    assert calls == []


@pytest.mark.parametrize("reason", ["REGISTRY_UNAVAILABLE", "REGISTRY_HASH_MISMATCH", "RELEASE_MISMATCH", "EXPLAIN_NOT_IN_RELEASE"])
def test_registry_failure_cannot_fall_back_to_old_packaged_catalogue(monkeypatch, reason):
    calls = []
    def unavailable(rid):
        raise RegistryError(reason)
    monkeypatch.setattr(handler, "load_registry", unavailable)
    monkeypatch.setattr(handler, "claim_daily_attempt", lambda: calls.append(True))
    response = handler.handler(event(), None)
    assert response["statusCode"] == 503 and body(response)["reason"] == reason
    assert body(response)["citations"] == [] and calls == []


@pytest.mark.parametrize("admission,reason", [("DAILY_QUOTA_EXHAUSTED", "DAILY_QUOTA_EXHAUSTED"),
                                            ("QUOTA_UNAVAILABLE", "QUOTA_UNAVAILABLE"), ("unexpected", "QUOTA_UNAVAILABLE")])
def test_denied_quota_returns_trusted_evidence_without_a_model_call(monkeypatch, trusted, admission, reason):
    calls = []
    monkeypatch.setattr(handler, "claim_daily_attempt", lambda: admission)
    monkeypatch.setattr(handler, "_compose", lambda _: calls.append(True))
    result = body(handler.handler(event(template="attacker"), None))
    assert result["source"] == "template" and result["reason"] == reason and calls == []
    assert result["text"] == trusted["subjects"]["facility:" + HERO]["fallback"]["text"]
    assert len(result["cited_facts"]) == 3


@pytest.mark.parametrize("error,reason", [(RuntimeError("upstream secret"), "MODEL_UNAVAILABLE"),
                                        (URLError(TimeoutError("upstream secret")), "MODEL_TIMEOUT")])
def test_upstream_failure_is_sanitized_and_never_retried(monkeypatch, error, reason):
    calls = []
    def fail(_):
        calls.append(True)
        raise error
    monkeypatch.setattr(handler, "_compose", fail)
    result = body(handler.handler(event(), None))
    assert result["reason"] == reason and len(calls) == 1
    assert "upstream secret" not in json.dumps(result)


def test_model_off_and_short_lambda_deadline_preserve_quota(monkeypatch):
    calls = []
    monkeypatch.setattr(handler, "claim_daily_attempt", lambda: calls.append(True))
    monkeypatch.setenv("MODEL_OFF", "true")
    assert body(handler.handler(event(), None))["reason"] == "MODEL_OFF"
    monkeypatch.delenv("MODEL_OFF")
    context = type("Context", (), {"get_remaining_time_in_millis": lambda _: 26_000})()
    assert body(handler.handler(event(), context))["reason"] == "REQUEST_DEADLINE"
    assert calls == []


def test_http_guards_keep_function_url_as_only_cors_owner():
    preflight = handler.handler({"httpMethod": "OPTIONS"}, None)
    assert preflight == {"statusCode": 204, "headers": {}, "body": ""}
    response = handler.handler({"httpMethod": "GET"}, None)
    assert response["statusCode"] == 405
    assert not any(key.lower().startswith("access-control-") for key in response["headers"])


def test_paid_transport_keeps_token_cap_single_attempt_and_fixed_timeout(monkeypatch, trusted):
    import urllib.request
    calls = []
    text = valid_model(trusted)
    response = json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}).encode()
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            assert limit == handler.MAX_MODEL_BODY + 1
            return response
    def open_request(request, *, timeout):
        calls.append((request, timeout))
        return Reply()
    monkeypatch.setenv("META_API_KEY", "test-only")
    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    result = body(handler.handler(event(), None))
    assert result["source"] == "model" and result["fact_ids"] == ["generator_table", "permit_hours"]
    assert len(calls) == 1 and calls[0][1] == 25
    request = json.loads(calls[0][0].data)
    assert request["max_output_tokens"] == 3072 and request["reasoning"] == {"effort": "minimal"}
    assert len(request["input"][0]["content"][0]["text"]) <= handler.MAX_INPUT + 100
    assert "test-only" not in request["input"][0]["content"][0]["text"]
    prompt = json.loads(request["input"][0]["content"][0]["text"])
    assert [fact["id"] for fact in prompt["facts"]] == ["F1", "F2", "F3"]
    assert "generator_table" not in request["input"][0]["content"][0]["text"]


def test_deadline_exhaustion_after_admission_does_not_start_model(monkeypatch):
    calls = []
    times = iter([30_000, 26_000])
    context = type("Context", (), {"get_remaining_time_in_millis": lambda _: next(times)})()
    monkeypatch.setattr(handler, "claim_daily_attempt", lambda: calls.append("admission") or "ALLOWED")
    monkeypatch.setattr(handler, "_compose", lambda _: calls.append("model"))
    assert body(handler.handler(event(), context))["reason"] == "REQUEST_DEADLINE"
    assert calls == ["admission"]


@pytest.mark.parametrize("citation", ["F0", "F4", "f1", "F01", "annual-invented-hash", "generator_table"])
def test_unknown_or_hallucinated_prompt_alias_fails_closed(monkeypatch, trusted, citation):
    output = json.loads(valid_model(trusted))
    output["paragraphs"][0]["fact_ids"] = [citation]
    calls = []
    monkeypatch.setattr(handler, "_compose", lambda payload: calls.append(payload) or json.dumps(output))
    result = body(handler.handler(event(), None))
    assert result["source"] == "template" and result["reason"] == "INVALID_CITATION"
    assert len(calls) == 1
    assert all(f["id"] in {"generator_table", "permit_hours", "campus_water"} for f in result["cited_facts"])


def test_aliases_map_to_current_prompt_order_and_response_keeps_original_ids(monkeypatch, trusted):
    facts = trusted["subjects"]["facility:" + HERO]["facts"]
    facts[:] = [facts[2], facts[0], facts[1]]
    seen = []
    def compose(payload):
        seen.extend(payload["facts"])
        # The model may return paragraphs in a different order from the prompt.
        return json.dumps({"paragraphs": [
            {"text": payload["facts"][1]["text"], "fact_ids": ["F2"]},
            {"text": payload["facts"][0]["text"], "fact_ids": ["F1"]}]})
    monkeypatch.setattr(handler, "_compose", compose)
    result = body(handler.handler(event(), None))
    assert [f["id"] for f in seen] == ["F1", "F2", "F3"]
    assert result["source"] == "model"
    assert result["fact_ids"] == ["generator_table", "campus_water"]
    assert [p["fact_ids"] for p in result["paragraphs"]] == [["generator_table"], ["campus_water"]]
    assert [f["id"] for f in result["cited_facts"]] == ["generator_table", "campus_water"]
    assert [c["fact_id"] for c in result["citations"]] == ["generator_table", "campus_water"]
    assert result["release_id"] == RELEASE and result["subject"]["id"] == HERO
    assert [f["id"] for f in facts] == ["campus_water", "generator_table", "permit_hours"]


def test_valid_alias_with_wrong_fact_text_does_not_bypass_scope_or_number_checks(monkeypatch, trusted):
    output = json.loads(valid_model(trusted))
    output["paragraphs"][0]["fact_ids"] = ["F2"]
    monkeypatch.setattr(handler, "_compose", lambda payload: json.dumps(output))
    result = body(handler.handler(event(), None))
    assert result["source"] == "template" and result["reason"] == "UNBOUND_NUMERAL"


def test_aliases_do_not_weaken_one_quantitative_fact_per_paragraph(monkeypatch, trusted):
    facts = trusted["subjects"]["facility:" + HERO]["facts"]
    output = {"paragraphs": [{"text": facts[0]["text"] + " " + facts[1]["text"], "fact_ids": ["F1", "F2"]}]}
    monkeypatch.setattr(handler, "_compose", lambda payload: json.dumps(output))
    result = body(handler.handler(event(), None))
    assert result["source"] == "template" and result["reason"] == "AMBIGUOUS_QUANTITATIVE_CITATIONS"


def test_permit_document_explanation_is_pinned_and_does_not_create_a_facility_match(monkeypatch, trusted):
    from copy import deepcopy
    subject = deepcopy(trusted["subjects"]["facility:" + HERO])
    subject.update(kind="permit_document", id="74094-1", title="Virginia permit 74094-1")
    trusted["subjects"]["permit_document:74094-1"] = subject
    calls = []
    monkeypatch.setattr(handler, "_compose", lambda payload: calls.append(payload) or valid_model(trusted))
    result = body(handler.handler(event(subject={"kind":"permit_document","id":"74094-1"}), None))
    assert result["source"] == "model" and len(calls) == 1
    assert result["release_id"] == RELEASE
    assert result["subject"] == {"kind":"permit_document","id":"74094-1"}
    assert "current effectiveness is unknown" in result["text"]
    unknown = body(handler.handler(event(subject={"kind":"facility","id":"74094-1"}), None))
    assert unknown["reason"] == "UNKNOWN_SUBJECT" and len(calls) == 1


def test_unpinned_permit_document_request_cannot_load_or_consume_quota(monkeypatch):
    calls = []
    monkeypatch.setattr(handler, "load_registry", lambda _: calls.append("registry"))
    monkeypatch.setattr(handler, "claim_daily_attempt", lambda: calls.append("quota"))
    result = handler.handler({"body":json.dumps({"subject":{"kind":"permit_document","id":"74094-1"}})}, None)
    assert result["statusCode"] == 400 and calls == []
