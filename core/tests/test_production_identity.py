"""Real audit failures and counterexamples for identity/location safeguards."""
import copy
import json
from pathlib import Path

import pytest

from wattdocket.production.identity import (
    condition_metadata, match_virginia_permits, permit_metadata, review_facilities,
)
from wattdocket.production.sources_facilities import normalize_echo


@pytest.fixture
def reviewed():
    return json.loads((Path(__file__).parent / "fixtures/identity_review_2026_11_03.json").read_text())


def record(reviewed, fid):
    return next(f for f in reviewed["facilities"] if f["id"] == "echo:" + fid)


def test_scout_cross_county_permit_never_attaches_despite_identical_name(reviewed):
    scout = record(reviewed, "110071294203")
    result = match_virginia_permits(scout, reviewed["permits"])
    assert result["matches"] == []
    assert result["identity_match"]["status"] == "conflict"
    assert result["match_basis"] is None
    assert result["candidate_matches"][0]["registration"] == "52629-4"
    assert "county_conflict" in result["candidate_matches"][0]["reason_codes"]
    assert result["candidate_matches"][0]["attaches_facts"] is False


def test_same_name_in_same_county_stays_candidate_when_identifiers_differ(reviewed):
    tecfusions = record(reviewed, "110001894040")
    result = match_virginia_permits(tecfusions, reviewed["permits"])
    assert result["matches"] == []
    assert result["identity_match"]["status"] == "candidate"
    assert result["conflicts"][0]["reason_codes"] == ["registration_not_confirmed"]


def test_true_registration_and_county_link_survives_a_different_record_name(reviewed):
    amazon = record(reviewed, "110031223771")
    amazon["name"] = "A renamed operator must not destroy regulator identity"
    result = match_virginia_permits(amazon, reviewed["permits"])
    assert [p["registration"] for p in result["matches"]] == ["73868-1"]
    assert result["identity_match"]["status"] == "matched"
    assert result["match_basis"] == "county_and_registration"
    assert result["identity_match"]["matched_source_ids"] == result["matched_source_ids"]
    assert all(s.endswith("73868") for s in result["matched_source_ids"])
    amazon["state"] = "TX"
    assert match_virginia_permits(amazon, reviewed["permits"])["matches"] == []


def test_exact_registration_without_matching_county_is_not_sufficient(reviewed):
    amazon = record(reviewed, "110031223771")
    permit = next(p for p in reviewed["permits"] if p["registration"] == "73868-1")
    permit["county"] = "Henrico Co."
    assert match_virginia_permits(amazon, [permit])["matches"] == []


def test_name_only_without_air_id_cannot_be_verified(reviewed):
    scout = record(reviewed, "110071294203")
    scout["air_records"] = []
    result = match_virginia_permits(scout, reviewed["permits"])
    assert not result["matches"] and result["identity_match"]["status"] == "candidate"


def test_real_amazon_mailbox_and_source_derived_state_conflict_withhold_all_four(reviewed):
    result = review_facilities(reviewed["facilities"])
    for fid in ["110070784059", "110071163341", "110071163349", "110071854350"]:
        quality = result["echo:" + fid]["location_quality"]
        assert quality["status"] == "suspect" and not quality["map_eligible"]
        assert "state_coordinate_conflict" in quality["reason_codes"]
        assert "shared_suspect_contact_location" in quality["reason_codes"]
        assert quality["evidence"]["coordinate_derived_county_fips"] == "53005"
        assert quality["physical_site_verified"] is False
    assert result["echo:110070784059"]["location_quality"]["address_type"] == "mailing"


def test_contact_warning_propagates_to_peer_without_mailbox_text():
    common = {"lat": 40.0, "lon": -100.0, "city": "Example", "state": "NE"}
    records = [{**common, "id": "record:1", "name": "Operator", "address_line": "42 Main St PMB 443"},
               {**common, "id": "record:2", "name": "Other name", "address_line": "42 Main Street"}]
    result = review_facilities(records)
    assert not result["record:2"]["location_quality"]["map_eligible"]
    assert result["record:2"]["location_quality"]["reason_codes"] == ["shared_suspect_contact_location"]


def test_real_shared_rackspace_records_are_candidates_never_merged(reviewed):
    subset = [record(reviewed, fid) for fid in ["110035370504", "110061671944"]]
    result = review_facilities(subset)
    assert len(result) == 2
    for data in result.values():
        assert data["site_identity"] == {"status": "candidate_group", "physical_site_id": None, "merge_verified": False}
        assert data["duplicate_candidates"]
        assert all(not x["merge_verified"] for x in data["duplicate_candidates"])
        assert data["location_quality"]["map_eligible"]


def test_shared_campus_is_not_a_mailbox_or_an_automatic_merge():
    common = {"lat": 40.0, "lon": -100.0, "address_line": "42 Main St", "city": "Example", "state": "NE"}
    result = review_facilities([{**common, "id": "a", "name": "Building A"}, {**common, "id": "b", "name": "Building B"}])
    assert result["a"]["location_quality"]["map_eligible"]
    assert result["b"]["location_quality"]["map_eligible"]
    assert result["a"]["site_identity"]["physical_site_id"] is None
    assert result["a"]["duplicate_candidates"][0]["record_ids"] == ["a", "b"]


@pytest.mark.parametrize("method", ["Zip Code Centroid", "State Centroid"])
def test_centroid_coordinates_are_not_facility_pins(method):
    result = review_facilities([{"id": "a", "lat": 38, "lon": -77,
                                 "coordinate_quality": {"collection_method": method}}])
    assert not result["a"]["location_quality"]["map_eligible"]
    assert "geographic_centroid" in result["a"]["location_quality"]["reason_codes"]


def test_missing_or_invalid_coordinates_never_become_zero_or_map_pins():
    for point in [(None, None), (float("nan"), -77), (True, -77), (38, float("inf")), (100, -77)]:
        raw = {"id": "a", "lat": point[0], "lon": point[1]}
        result = review_facilities([raw])["a"]["location_quality"]
        assert not result["map_eligible"] and result["status"] == "unknown"


@pytest.mark.parametrize("accuracy", [True, False, -1, float("inf"), float("nan"), "30", "unknown"])
def test_invalid_normalized_coordinate_accuracy_withholds_location(accuracy):
    raw = {"id": "a", "lat": 38, "lon": -77, "coordinate_accuracy_m": accuracy}
    quality = review_facilities([raw])["a"]["location_quality"]
    assert not quality["map_eligible"]
    assert "invalid_coordinate_accuracy" in quality["reason_codes"]


def test_failed_source_accuracy_parsing_remains_visible_in_quality():
    raw = {"id": "a", "lat": 38, "lon": -77, "coordinate_accuracy_m": None,
           "coordinate_quality": {"reported_accuracy": "-1"}}
    assert not review_facilities([raw])["a"]["location_quality"]["map_eligible"]


def test_only_selected_coordinate_source_controls_derived_state_check():
    raw = {"id": "a", "lat": 38, "lon": -77, "state": "VA", "coordinate_source": "icis_air",
           "combined_record": {"FacDerivedStctyFIPS": "53005"}}
    assert review_facilities([raw])["a"]["location_quality"]["map_eligible"]
    raw["coordinate_source"] = "frs"
    assert not review_facilities([raw])["a"]["location_quality"]["map_eligible"]


def test_zip_extension_is_not_a_postal_conflict_but_another_zip_is():
    raw = {"id": "a", "lat": 38, "lon": -77, "zip": "20147-1234", "coordinate_source": "frs",
           "combined_record": {"FacDerivedZip": "20147"}}
    assert review_facilities([raw])["a"]["location_quality"]["map_eligible"]
    raw["combined_record"]["FacDerivedZip"] = "20148"
    assert "postal_coordinate_conflict" in review_facilities([raw])["a"]["location_quality"]["reason_codes"]


def test_review_is_deterministic_and_does_not_modify_archived_inputs(reviewed):
    before = copy.deepcopy(reviewed)
    result = review_facilities(reviewed["facilities"])
    reverse = review_facilities(list(reversed(reviewed["facilities"])))
    assert result == reverse
    assert reviewed == before


def test_distinct_programs_do_not_acquire_effectiveness_or_supersession_from_dates(reviewed):
    permits = [p for p in reviewed["permits"] if p["registration"].startswith("21527-")]
    assert {p["program"] for p in permits} == {"Article 1 - Title V", "Article 6 - mNSR"}
    for permit in permits:
        facts = permit_metadata({**permit, "document": {"status": "parsed"}})
        assert facts["registration"] == permit["registration"] and facts["county_fips"] == "117"
        assert facts["document_status"] == "issued" and facts["parse_status"] == "parsed"
        assert facts["effective_status"] == "unknown" and facts["is_current_effective_limit"] is None
        assert facts["supersedes"] == facts["superseded_by"] == []
        assert not facts["latest_effective_status_verified"]


def test_condition_scope_does_not_invent_per_generator_or_annual_allowances():
    hours = {"metric": "generator_hours_limit", "unit": "hours/year per generator", "value": 100,
             "citation": {"quote": "Group A generators combined shall not operate more than 100 hours per year for testing."}}
    assert condition_metadata(hours)["unit"] == "hours/year"
    assert condition_metadata(hours)["applies_to_entire_site"] is False
    assert condition_metadata(hours)["is_current_effective_limit"] is None
    fuel = {"metric": "fuel_throughput_limit", "unit": "gallons/year", "citation": {"quote": "The unit shall consume no more than 500 gallons during construction."}}
    assert condition_metadata(fuel)["unit"] == "gallons (period not verified)"
    fuel["citation"]["quote"] = "The group shall consume no more than 500 gallons in each consecutive 365-day period."
    assert condition_metadata(fuel)["unit"] == "gallons/year"


def test_source_status_does_not_assert_operating_data_center():
    rows = normalize_echo([{"RegistryID": "110000000001", "FacName": "Support office"}],
                          [{"RegistryID": "110000000001", "SourceID": "X1", "AIRStatus": "Operating"}])
    assert rows[0]["status"] == "operating"
    assert rows[0]["is_operating_datacenter_verified"] is False
    assert "AIR program" in rows[0]["status_basis"]


def test_source_boolean_coordinates_and_accuracy_are_not_coerced_to_numbers():
    invalid_point = normalize_echo([{"RegistryID": "110000000001", "FacLat": True, "FacLong": -77}], [])[0]
    assert invalid_point["lat"] is None and invalid_point["lon"] is None
    invalid_accuracy = normalize_echo([{"RegistryID": "110000000001", "FacLat": 38, "FacLong": -77, "FacAccuracyMeters": True}], [])[0]
    assert invalid_accuracy["coordinate_accuracy_m"] is None
    assert not review_facilities([invalid_accuracy])[invalid_accuracy["id"]]["location_quality"]["map_eligible"]


def test_review_sample_preserves_status_and_program_id_uncertainty(reviewed):
    assert len({f["state"] for f in reviewed["facilities"]}) == 7
    assert {f["status"] for f in reviewed["facilities"]} == {"closed", "operating", "unknown", "proposed", "under_construction"}
    program = record(reviewed, "TXR1551ZU")
    assert program["identity_kind"] == "program_record" and program["registry_id"] is None
    assert not match_virginia_permits(program, reviewed["permits"])["matches"]
    for fid in ("110070119832", "110071355440"):
        warehouse = record(reviewed, fid)
        assert "518210" in warehouse["naics"]
        assert match_virginia_permits(warehouse, reviewed["permits"])["identity_match"]["status"] == "unmatched"
