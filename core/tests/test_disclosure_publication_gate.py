"""Adversarial checks against the emitted public contract, not matching helpers."""
import copy

import pytest

from wattdocket.production.quality import RecordEvidenceError, validate_disclosure_publication


def sample():
    sha = "a" * 64
    evidence = {"id": "government-address", "content_sha256": sha, "verified": True,
                "url": "https://county.gov/report.pdf", "publisher": "County",
                "retrieved_at": "2026-11-03T12:00:00Z", "excerpts": ["Example at 10 Main Street"],
                "representation": "publisher_pdf", "page": 3,
                "receipt": {"url": "https://county.gov/report.pdf", "sha256": sha,
                            "raw_key": f"raw/{sha}.gz", "retrieved_at": "2026-11-03T12:00:00Z"}}
    expected = {"id": "echo:123", "registry_id": "123", "name": "Example", "address": "10 Main Street", "city": "Town", "state": "VA", "program_id": "VA123"}
    link = {"disclosure_location_id": "operator:campus", "record_id": "echo:123", "relationship": "within_disclosure_location",
            "verification_status": "verified", "allocation_allowed": False, "same_physical_scope_verified": False,
            "inventory_allocation": False, "expected_record": expected,
            "evidence_ids": ["government-address"], "evidence": [evidence]}
    row = {"observation_id": "operator:campus:2025:electricity", "disclosure_location_id": "operator:campus",
           "source_id": "operator_report", "reporting_scope": "campus", "metric": "electricity_consumption", "value": 0,
           "unit": "MWh", "data_year": 2025, "period_start": "2025-01-01T00:00:00Z", "period_end": "2026-01-01T00:00:00Z",
           "source_sha256": "b" * 64, "source_url": "https://operator.example/report.pdf", "publisher": "Operator", "source_page": 5,
           "reporting_basis": "operator_reported", "methodology_note": "Campus aggregate, not metering for each building",
           "retrieved_at": "2026-11-03T12:00:00Z", "inventory_allocation": False, "facility_binding_verified": False,
           "value_origin": "reported_disclosure",
           "source_receipt": {"url": "https://operator.example/report.pdf", "sha256": "b" * 64,
                              "raw_key": f"raw/{'b' * 64}.gz", "retrieved_at": "2026-11-03T12:00:00Z"}}
    loc = {"disclosure_location_id": "operator:campus", "location_label": "Example campus", "reporting_scope": "campus",
           "source_id": "operator_report", "observations": [row], "record_links": [link]}
    cov = {"disclosure_locations": 1, "disclosure_observations": 1, "records_with_linked_disclosures": 1,
           "reviewed_disclosure_links": 1, "actual_power_records": 0, "actual_water_records": 0, "actual_carbon_records": 0,
           "reported_electricity_locations": 1, "reported_water_locations": 0, "reported_emissions_locations": 0}
    payload = {"release_id": "release-test", "built_at": "2026-11-03T12:00:00Z", "locations": [loc], "identity_evidence": [evidence],
               "observation_count": 1, "metric_counts": {"electricity_consumption": 1}, "coverage": cov}
    card = {"release_id": "release-test", "facility": {**expected, "record_summary": [{"label": "AIR program ID", "value": "VA123"}]},
            "disclosures": [{"location": loc, "link": link}], "claims": [{"id": "power-mw", "scope": "facility", "value": "NOT_PUBLISHED"}]}
    return payload, {"echo_123": card}, copy.deepcopy(cov)


def test_reported_zero_remains_valid_without_becoming_a_facility_measurement():
    payload, cards, coverage = sample()
    validate_disclosure_publication(payload, cards, coverage)
    assert payload["locations"][0]["observations"][0]["value"] == 0
    assert coverage["actual_power_records"] == 0


def test_revalidated_cached_bytes_keep_original_retrieval_separate_from_last_check():
    payload, cards, coverage = sample()
    row = payload["locations"][0]["observations"][0]
    row["retrieved_at"] = "2026-10-01T12:00:00Z"
    row["checked_at"] = "2026-11-03T12:00:00Z"
    row["source_receipt"].update(retrieved_at=row["retrieved_at"], checked_at=row["checked_at"], http_status=304)
    validate_disclosure_publication(payload, cards, coverage)
    row["retrieved_at"] = row["checked_at"]
    with pytest.raises(RecordEvidenceError, match="immutable capture receipt"):
        validate_disclosure_publication(payload, cards, coverage)


@pytest.mark.parametrize("field", ["url", "sha256", "raw_key", "retrieved_at", "missing"])
def test_observation_receipt_must_match_source_identity_and_retrieval(field):
    payload, cards, coverage = sample()
    row = payload["locations"][0]["observations"][0]
    if field == "missing":
        row["source_receipt"] = None
    else:
        row["source_receipt"][field] = "different"
    with pytest.raises(RecordEvidenceError, match="immutable capture receipt"):
        validate_disclosure_publication(payload, cards, coverage)


@pytest.mark.parametrize("problem", ["nan", "negative", "boolean", "unit", "year", "period", "hash", "source", "locator", "basis", "scope", "allocated", "duplicate_observation"])
def test_observation_mutations_are_rejected(problem):
    payload, cards, coverage = sample()
    loc = payload["locations"][0]
    row = loc["observations"][0]
    changes = {"nan": ("value", float("nan")), "negative": ("value", -1), "boolean": ("value", True), "unit": ("unit", "MW"),
               "year": ("data_year", 2026), "period": ("period_end", "2025-12-31T00:00:00Z"), "hash": ("source_sha256", "bad"),
               "source": ("source_url", "http://operator.example/report.pdf"), "locator": ("source_page", None),
               "basis": ("reporting_basis", None), "scope": ("reporting_scope", "building"), "allocated": ("inventory_allocation", True)}
    if problem == "duplicate_observation":
        loc["observations"].append(copy.deepcopy(row))
    else:
        key, value = changes[problem]
        row[key] = value
    with pytest.raises(RecordEvidenceError):
        validate_disclosure_publication(payload, cards, coverage)


@pytest.mark.parametrize("problem", ["allocation", "equal_scope_flag", "unproven_equal_boundary", "address", "registry", "program", "receipt", "passage", "no_evidence", "card_value", "missing_card_ref", "duplicate_card_ref", "count", "actual_claim", "release"])
def test_identity_allocation_and_card_cross_reference_mutations_are_rejected(problem):
    payload, cards, coverage = sample()
    card = cards["echo_123"]
    link = payload["locations"][0]["record_links"][0]
    if problem == "allocation":
        link["inventory_allocation"] = True
    elif problem == "equal_scope_flag":
        link.update(relationship="same_physical_scope", inventory_allocation=True)
    elif problem == "unproven_equal_boundary":
        link.update(relationship="same_physical_scope", inventory_allocation=True, allocation_allowed=True, same_physical_scope_verified=True)
    elif problem == "address":
        card["facility"]["address"] = "20 Main Street"
    elif problem == "registry":
        card["facility"]["registry_id"] = "999"
    elif problem == "program":
        card["facility"]["record_summary"] = []
    elif problem == "receipt":
        payload["identity_evidence"][0]["receipt"]["sha256"] = "c" * 64
    elif problem == "passage":
        payload["identity_evidence"][0]["excerpts"] = []
    elif problem == "no_evidence":
        link["evidence_ids"] = []
    elif problem == "card_value":
        card["disclosures"] = copy.deepcopy(card["disclosures"])
        card["disclosures"][0]["location"]["observations"][0]["value"] = 100
    elif problem == "missing_card_ref":
        card["disclosures"] = []
    elif problem == "duplicate_card_ref":
        card["disclosures"].append(copy.deepcopy(card["disclosures"][0]))
    elif problem == "count":
        coverage["actual_power_records"] = 1
    elif problem == "actual_claim":
        card["claims"][0]["value"] = 0
    else:
        card["release_id"] = "another-release"
    with pytest.raises(RecordEvidenceError):
        validate_disclosure_publication(payload, cards, coverage)


def test_candidate_links_remain_public_but_cannot_appear_as_verified_card_evidence():
    payload, cards, coverage = sample()
    link = payload["locations"][0]["record_links"][0]
    link.update(relationship="candidate", verification_status="candidate")
    cards["echo_123"]["disclosures"] = []
    for obj in (coverage, payload["coverage"]):
        obj.update(records_with_linked_disclosures=0, reviewed_disclosure_links=0)
    validate_disclosure_publication(payload, cards, coverage)


def test_empty_reporting_collection_is_valid_without_fake_actuals():
    payload, cards, coverage = sample()
    payload.update(locations=[], identity_evidence=[], observation_count=0, metric_counts={})
    cards["echo_123"]["disclosures"] = []
    payload["coverage"] = {key: 0 for key in coverage}
    coverage = dict(payload["coverage"])
    validate_disclosure_publication(payload, cards, coverage)


def test_primary_address_proven_osm_coverage_requires_its_exact_identity_and_no_invented_frs():
    payload, cards, coverage = sample()
    card = cards.pop('echo_123')
    fid = 'osm:way:578412815'
    link = payload['locations'][0]['record_links'][0]
    link['record_id'] = fid
    link['expected_record'].update(id=fid, registry_id=None, program_id=None)
    card['facility'].update(id=fid, registry_id=None, record_summary=[])
    cards['osm_way_578412815'] = card
    validate_disclosure_publication(payload, cards, coverage)
    card['facility']['registry_id'] = '578412815'
    with pytest.raises(RecordEvidenceError):
        validate_disclosure_publication(payload, cards, coverage)
    card['facility']['registry_id'] = None
    card['facility']['address'] = '999 Wrong Road'
    with pytest.raises(RecordEvidenceError):
        validate_disclosure_publication(payload, cards, coverage)
