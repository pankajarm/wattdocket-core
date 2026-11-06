"""Adversarial prose checks use only cited fact text, never global numerals."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api/lambda_explain"))
from registry_loader import RegistryError
from response_validation import InvalidExplanation, prompt_facts, validate_model_response, validate_subject


def subject():
    registry = json.loads((ROOT / "tests/fixtures/explain_registry.json").read_text())
    return registry["subjects"]["facility:echo:110072155814"]


def response(facts):
    return {"paragraphs": [{"text": facts[0]["text"], "fact_ids": [facts[0]["id"]]},
                           {"text": facts[1]["text"], "fact_ids": [facts[1]["id"]]}]}


def test_valid_scoped_paragraphs_retain_citations():
    facts = subject()["facts"]
    result = validate_model_response(json.dumps(response(facts)), facts)
    assert result["fact_ids"] == ["generator_table", "permit_hours"]
    assert "not actual use" in result["text"]


@pytest.mark.parametrize("damage,reason", [
    ("uncited_number", "UNBOUND_NUMERAL"), ("unit_swap", "UNBOUND_QUANTITY_UNIT"),
    ("missing_qualifier", "MISSING_SCOPE_QUALIFIER"), ("actual_use", "UNSUPPORTED_SCOPE_CLAIM"),
    ("made_up_absence", "UNSUPPORTED_SCOPE_CLAIM"), ("legal", "UNSUPPORTED_SCOPE_CLAIM"),
    ("unknown_fact", "INVALID_CITATION"), ("source_url", "INVALID_MODEL_RESPONSE"),
    ("extra_field", "INVALID_MODEL_RESPONSE"), ("numeric_metadata", "UNBOUND_NUMERAL"),
])
def test_bad_prose_returns_explicit_validation_reason(damage, reason):
    facts = subject()["facts"]
    output = response(facts)
    paragraph = output["paragraphs"][0]
    if damage == "uncited_number":
        paragraph["text"] += " The associated water figure is 42 million gallons."
    elif damage == "unit_swap":
        paragraph["text"] = paragraph["text"].replace("401.4 MW", "401.4 MWh")
    elif damage == "missing_qualifier":
        paragraph["text"] = paragraph["text"].replace("current effectiveness is unknown", "it is authorized")
    elif damage == "actual_use":
        paragraph["text"] += " The facility uses 401.4 MW."
    elif damage == "made_up_absence":
        paragraph["text"] += " Water information is not published."
    elif damage == "legal":
        paragraph["text"] += " The facility is compliant."
    elif damage == "unknown_fact":
        paragraph["fact_ids"] = ["not-in-registry"]
    elif damage == "source_url":
        paragraph["text"] += " https://attacker.example/"
    elif damage == "extra_field":
        output["trusted"] = True
    else:
        facts[0]["numbers"].append({"value":991234567,"display":"991234567","unit":"MW","label":"not in actual fact text"})
        paragraph["text"] += " It has 991234567 MW."
    with pytest.raises(InvalidExplanation, match=reason):
        validate_model_response(json.dumps(output), facts)


def test_number_in_other_cited_paragraph_still_cannot_leak_to_this_paragraph():
    facts = subject()["facts"]
    output = response(facts)
    output["paragraphs"][1] = {"text": facts[2]["text"], "fact_ids": [facts[2]["id"]]}
    output["paragraphs"][0]["text"] += " A water reading is 42 million gallons."
    with pytest.raises(InvalidExplanation, match="UNBOUND_NUMERAL"):
        validate_model_response(json.dumps(output), facts)


def test_numbers_in_ids_urls_or_source_locators_are_not_allowances():
    facts = subject()["facts"]
    facts[0]["evidence"][0]["locator"] = "Page 991234567"
    output = response(facts)
    output["paragraphs"][0]["text"] += " The figure is 991234567 MW."
    with pytest.raises(InvalidExplanation, match="UNBOUND_NUMERAL"):
        validate_model_response(json.dumps(output), facts)


@pytest.mark.parametrize("damage", ["qualifier", "fallback", "hash", "url", "number", "duplicate"])
def test_bad_trusted_subject_fails_closed_before_quota(damage):
    item = subject()
    if damage == "qualifier":
        item["facts"][0]["required_qualifiers"].append("not in source text")
    elif damage == "fallback":
        item["fallback"]["text"] = "Invented fallback 999 MW."
    elif damage == "hash":
        item["facts"][0]["evidence"][0]["sha256"] = "wrong"
    elif damage == "url":
        item["facts"][0]["evidence"][0]["url"] = "http://metadata.internal/"
    elif damage == "number":
        item["facts"][0]["numbers"][0]["display"] = "999"
    else:
        item["facts"][1]["id"] = item["facts"][0]["id"]
    with pytest.raises(RegistryError):
        validate_subject(item)


def test_prompt_budget_preserves_whole_fact_and_required_qualifiers():
    item = subject()
    selected = prompt_facts(item, 800)
    assert selected
    assert len(selected) < len(item["facts"])
    assert all(fact in item["facts"] for fact in selected)
    assert all(all(q in f["text"] for q in f["required_qualifiers"]) for f in selected)


def test_model_must_include_quantitative_content_from_each_cited_fact():
    facts = subject()["facts"]
    output = response(facts)
    output["paragraphs"][0]["text"] = "This is nameplate capacity, not actual use; current effectiveness is unknown."
    with pytest.raises(InvalidExplanation, match="INSUFFICIENT_EVIDENCE_COVERAGE"):
        validate_model_response(json.dumps(output), facts)


@pytest.mark.parametrize("replacement", ["-401.4 MW", "−401.4 MW", "- 401.4 MW", "minus 401.4 MW"])
def test_negative_demand_cannot_borrow_positive_value(replacement):
    facts = subject()["facts"]
    output = response(facts)
    output["paragraphs"][0]["text"] = facts[0]["text"].replace("401.4 MW", replacement)
    with pytest.raises(InvalidExplanation, match="UNBOUND_NUMERAL"):
        validate_model_response(json.dumps(output), facts)


def test_signed_values_preserve_dates_and_do_not_change_positive_allowance():
    from response_validation import numerals, quantities
    assert numerals("2020-07-18T15:00:00Z") == {"2.02E+3", "7", "18", "0"}
    assert "-60.5" in numerals("-60.5 MW")
    assert "-60.5" not in numerals("60.5 MW")
    assert ("-60.5", "mw") in quantities("-60.5 MW")
    assert validate_model_response(json.dumps(response(subject()["facts"])), subject()["facts"])


@pytest.mark.parametrize("claim", ["It currently draws 401.4 MW.", "This is live telemetry.", "Its current load is 401.4 MW."])
def test_historical_disclaimer_does_not_authorize_positive_current_claim(claim):
    facts = subject()["facts"]
    facts[0]["kind"] = "historical_power_series"
    facts[0]["scope"] = "publisher-defined HPC meter"
    facts[0]["text"] += " This is historical, not live telemetry."
    facts[0]["required_qualifiers"].extend(["historical", "not live telemetry"])
    output = response(facts)
    output["paragraphs"][0]["text"] += " " + claim
    with pytest.raises(InvalidExplanation, match="UNSUPPORTED_SCOPE_CLAIM"):
        validate_model_response(json.dumps(output), facts)


@pytest.mark.parametrize("period", ["daily", "monthly", "per hour", "each day"])
def test_annual_total_cannot_be_recast_as_short_period(period):
    facts = subject()["facts"]
    facts[2]["kind"] = "annual_disclosure"
    output = {"paragraphs": [{"text": facts[2]["text"] + " Its " + period + " total is 42 million gallons.", "fact_ids": [facts[2]["id"]]},
                             {"text": facts[0]["text"], "fact_ids": [facts[0]["id"]]}]}
    with pytest.raises(InvalidExplanation, match="UNSUPPORTED_SCOPE_CLAIM"):
        validate_model_response(json.dumps(output), facts)


def test_cited_data_gap_can_be_explained_as_missing_measurement():
    fact = deepcopy(subject()["facts"][0])
    fact.update(id="gap", kind="data_gap", scope="inventory_record",
                text="There is no assigned, verified whole-facility electricity demand; missing values are unknown, not zero.",
                required_qualifiers=["whole-facility", "unknown, not zero"], numbers=[])
    output = {"paragraphs": [{"text": "No measurement is available for whole-facility electricity demand in this record; missing values remain unknown, not zero.",
                              "fact_ids": ["gap"]}]}
    assert validate_model_response(json.dumps(output), [fact])["fact_ids"] == ["gap"]


def test_quantitative_facts_cannot_share_paragraph_allowance_and_swap_values():
    facts = subject()["facts"]
    output = {"paragraphs": [{"text": facts[0]["text"] + " " + facts[2]["text"], "fact_ids": [facts[0]["id"], facts[2]["id"]]}]}
    with pytest.raises(InvalidExplanation, match="AMBIGUOUS_QUANTITATIVE_CITATIONS"):
        validate_model_response(json.dumps(output), facts)


@pytest.mark.parametrize("original,replacement", [("42 kBtu", "42 kgal"), ("42 kgal", "42 kBtu"),
                                                 ("42 MWh/year", "42 MWh/hour"), ("42 lb CO2e/MWh", "42 lb CO2e/year")])
def test_native_units_and_rate_denominators_are_not_interchangeable(original, replacement):
    fact = deepcopy(subject()["facts"][2])
    fact["text"] = "The historical campus total is " + original + "; it is not allocated to any inventory record."
    fact["required_qualifiers"] = ["historical", "campus total", "not allocated"]
    fact["numbers"] = [{"value": 42, "display": "42", "unit": original.split(" ", 1)[1]}]
    output = {"paragraphs": [{"text": fact["text"].replace(original, replacement), "fact_ids": [fact["id"]]}]}
    with pytest.raises(InvalidExplanation, match="UNBOUND_QUANTITY_UNIT"):
        validate_model_response(json.dumps(output), [fact])


@pytest.mark.parametrize('claim', [
    'It is currently effective.', 'These are current legal limits.', 'It is legally binding.',
    'The site is operating, so the permit is still valid.',
    'The permit remains active.', 'These conditions are in force.',
    'The listed limits still apply today.',
])
def test_recovered_permit_cannot_claim_current_legal_effect(claim):
    facts = subject()['facts']
    output = response(facts)
    output['paragraphs'][1]['text'] += ' ' + claim
    with pytest.raises(InvalidExplanation, match='UNSUPPORTED_SCOPE_CLAIM'):
        validate_model_response(json.dumps(output), facts)


def test_partial_amendment_cannot_be_promoted_to_complete_permit():
    from response_validation import _contradictions
    fact = {'kind': 'permit_document_review', 'scope': 'document_review',
            'text': 'This is a partial amendment, not a complete permit; current effectiveness is unknown.'}
    assert not _contradictions(fact['text'], [fact])
    assert _contradictions(fact['text'] + ' It provides a complete set of permit conditions.', [fact])


def test_site_activity_and_pending_history_do_not_authorize_active_permit_claim():
    from response_validation import _contradictions
    fact = {'kind': 'permit_history_review', 'scope': 'document_history',
            'text': 'A pending application is not final issuance. Current legal effectiveness is unknown; site activity does not establish an active permit.'}
    assert not _contradictions(fact['text'], [fact])
    assert _contradictions(fact['text'] + ' This is an active permit.', [fact])
    assert _contradictions(fact['text'] + ' The permit remains valid.', [fact])
    site = {'kind': 'regulator_site_identity', 'scope': 'regulatory_air_site',
            'text': 'The captured record reports Operating; site status is not permit legal effectiveness.'}
    assert not _contradictions(site['text'], [site])
    assert _contradictions(site['text'] + ' The permit remains active.', [site])
