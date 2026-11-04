"""Adversarial public-output mutations, independent of matching code."""
import copy

import pytest

from wattdocket.production.quality import RecordEvidenceError, validate_record_evidence


@pytest.fixture
def public_record():
    # A minimal emitted record: a valid Loudoun identifier, not a mock matcher.
    facility = {"id": "echo:test", "state": "VA", "class_verified": True, "site_id": None,
                "site_identity": {"physical_site_id": None, "merge_verified": False},
                "location_quality": {"status": "unknown", "map_eligible": True, "physical_site_verified": False},
                "identity_match": {"status": "matched", "method": "county_and_registration",
                                   "matched_source_ids": ["VA0000005110774269"]},
                "permit_limits": [{"registration": "74269-2", "county_fips": "107", "scope": "quoted_condition",
                                   "applies_to_entire_site": False, "effective_status": "unknown",
                                   "is_current_effective_limit": None, "latest_effective_status_verified": False}]}
    claim = {"id": "scenario-carbon", "scope": "scenario", "range_kind": "scenario", "badge": "MODELED",
             "inputs": {"facility_measurement": False}, "range": {"low": 1, "base": 2, "high": 3}}
    return {"card": {"facility": facility, "grid": {"subregion": "SRVC"}, "claims": [claim]}}, [copy.deepcopy(facility)]


def test_valid_minimal_public_evidence_does_not_need_unused_registry_summary(public_record):
    assert validate_record_evidence(*public_record) is None


def test_scout_style_cross_county_and_registration_conditions_reject(public_record):
    cards, entries = public_record
    cards["card"]["facility"]["permit_limits"][0].update(registration="52629-4", county_fips="087")
    with pytest.raises(RecordEvidenceError, match="Permit county/registration"):
        validate_record_evidence(cards, entries)


@pytest.mark.parametrize("mutation", ["name_match", "missing_ids", "malformed_id", "unsupported_id"])
def test_verified_label_cannot_substitute_for_identifier_evidence(public_record, mutation):
    cards, entries = public_record
    facility = cards["card"]["facility"]
    if mutation == "name_match":
        facility["identity_match"]["method"] = "exact_normalized_name_only"
    elif mutation == "missing_ids":
        facility["identity_match"]["matched_source_ids"] = []
    elif mutation == "malformed_id":
        facility["identity_match"]["matched_source_ids"] = ["74269"]
    else:
        facility["record_summary"] = [{"label": "AIR program ID", "value": "VA0000005115374122"}]
    with pytest.raises(RecordEvidenceError, match="identity|identifiers|classification"):
        validate_record_evidence(cards, entries)


@pytest.mark.parametrize("regional_binding", [True, False])
def test_unsafe_location_rejects_binding_or_leftover_scenario(public_record, regional_binding):
    cards, entries = public_record
    cards["card"]["facility"]["location_quality"]["map_eligible"] = False
    entries[0]["location_quality"]["map_eligible"] = False
    if not regional_binding:
        cards["card"]["grid"]["subregion"] = None
    with pytest.raises(RecordEvidenceError, match="Withheld location"):
        validate_record_evidence(cards, entries)


def test_withheld_location_without_context_or_scenario_is_publishable(public_record):
    cards, entries = public_record
    cards["card"]["facility"]["location_quality"]["map_eligible"] = False
    entries[0]["location_quality"]["map_eligible"] = False
    cards["card"].update(grid={"subregion": None}, claims=[])
    validate_record_evidence(cards, entries)


@pytest.mark.parametrize("field,value", [("site_id", "invented-campus"), ("site_identity", {"merge_verified": True})])
def test_source_record_cannot_be_promoted_to_physical_site(public_record, field, value):
    cards, entries = public_record
    cards["card"]["facility"][field] = value
    with pytest.raises(RecordEvidenceError, match="physical-site grouping"):
        validate_record_evidence(cards, entries)


@pytest.mark.parametrize("field,value", [("effective_status", "current"), ("latest_effective_status_verified", True), ("is_current_effective_limit", True)])
def test_legal_effectiveness_cannot_be_created_by_flipping_a_flag(public_record, field, value):
    cards, entries = public_record
    cards["card"]["facility"]["permit_limits"][0][field] = value
    with pytest.raises(RecordEvidenceError, match="current permit effectiveness"):
        validate_record_evidence(cards, entries)


def test_scenario_percentiles_are_not_accepted_as_utilization_choices(public_record):
    cards, entries = public_record
    cards["card"]["claims"][0]["range"] = {"p10": 1, "p50": 2, "p90": 3}
    with pytest.raises(RecordEvidenceError, match="percentiles"):
        validate_record_evidence(cards, entries)


@pytest.mark.parametrize("mutation", ["reported_badge", "actual_flag", "missing_measurement_scope"])
def test_scenario_cannot_masquerade_as_an_actual_observation(public_record, mutation):
    cards, entries = public_record
    claim = cards["card"]["claims"][0]
    if mutation == "reported_badge":
        claim["badge"] = "REPORTED"
    elif mutation == "actual_flag":
        claim["inputs"]["facility_measurement"] = True
    else:
        claim["inputs"] = {}
    with pytest.raises(RecordEvidenceError, match="measurement"):
        validate_record_evidence(cards, entries)


def test_unverified_name_candidate_cannot_keep_permit_conditions(public_record):
    cards, entries = public_record
    cards["card"]["facility"]["class_verified"] = entries[0]["class_verified"] = False
    with pytest.raises(RecordEvidenceError, match="Unverified identity"):
        validate_record_evidence(cards, entries)


def test_equipment_only_record_cannot_hide_a_wrong_permit_identity(public_record):
    cards, entries = public_record
    cards["card"]["facility"]["permit_limits"] = []
    cards["card"]["claims"].append({"id": "diesel-mw", "value": 42,
                                  "evidence_scope": "equipment_table_page", "is_complete_fleet": False,
                                  "permit_metadata": {"registration": "52629-4", "county_fips": "087"}})
    with pytest.raises(RecordEvidenceError, match="Permit county/registration"):
        validate_record_evidence(cards, entries)
