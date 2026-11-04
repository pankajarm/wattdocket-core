"""Facility-type evidence may only ever come from a regulator saying so."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from wattdocket.classify import VirginiaRegistry, apply_registry, name_key  # noqa: E402

CSV = pathlib.Path(__file__).parent.parent.parent / "data" / "deq_air_permits.csv"
pytestmark = pytest.mark.skipif(not CSV.exists(), reason="registry CSV absent")


@pytest.fixture(scope="module")
def registry() -> VirginiaRegistry:
    return VirginiaRegistry.load(CSV)


def test_registry_parses_the_commonwealths_own_list(registry: VirginiaRegistry):
    assert len(registry) > 150


def test_exact_name_match_finds_the_permit(registry: VirginiaRegistry):
    ev = registry.evidence_for("TECfusions Clarksville LLC")
    assert ev is not None
    assert "30142-3" in ev["class_label"]
    assert ev["class_verified"] is True


def test_spelling_variants_of_one_name_are_the_same_site():
    assert name_key("Amazon Data Services, Inc. - IAD 130") == \
           name_key("AMAZON DATA SERVICES INC IAD 130")


def test_an_unknown_name_gets_nothing(registry: VirginiaRegistry):
    assert registry.evidence_for("Paper Mill Of Nowhere") is None


def test_a_generic_name_does_not_match_across_states(registry: VirginiaRegistry):
    """The defect this guard exists for.

    "EQUINIX LLC" and "MICROSOFT" appear in the Virginia registry, and ECHO has
    facilities of exactly those names in Illinois, Oregon, New Jersey and Texas.
    Before the jurisdiction check, five cards claimed a Virginia air permit for a
    building in another state.
    """
    for state in ("IL", "OR", "NJ", "TX"):
        block = {"name": "EQUINIX LLC", "state": state,
                 "class_verified": False, "class_evidence": "none"}
        apply_registry(block, registry)
        assert block["class_verified"] is False, state
        assert "Virginia" not in block["class_evidence"], state


def test_a_virginia_facility_is_upgraded(registry: VirginiaRegistry):
    block = {"name": "VIENNA CLOUD", "state": "VA",
             "class_verified": False, "class_evidence": "none"}
    apply_registry(block, registry)
    assert block["class_verified"] is True
    assert block["class_match_method"] == "exact_normalised_name"
    assert "not text-extracted" in block["class_evidence"]


def test_lowercase_state_is_still_virginia(registry: VirginiaRegistry):
    block = {"name": "VIENNA CLOUD", "state": "va", "class_verified": False}
    apply_registry(block, registry)
    assert block["class_verified"] is True


def test_case_insensitive_registry_entries_are_fine(registry: VirginiaRegistry):
    spec = pathlib.Path(__file__).parent.parent / "tests" / "_tmp_registry.csv"
    spec.write_text("site,registration,issued,program,county,office,permit_url\n"
                    "ACME DATA HALL,1-1,01/01/2026,Article 6,Nowhere Co.,N,http://x\n")
    try:
        reg = VirginiaRegistry.load(spec)
        ev = reg.evidence_for("acme data hall")
        assert ev and "1-1" in ev["class_label"]
    finally:
        spec.unlink(missing_ok=True)


def test_source_id_carries_county_and_plant_id():
    from wattdocket.classify import source_id_parts
    assert source_id_parts("VA0000005110774269") == ("107", "74269")
    assert source_id_parts("TX0000004811300123") is None
    assert source_id_parts("") is None


def test_a_tbd_address_attaches_by_registration_number(registry: VirginiaRegistry):
    """ECHO: "STACK INFRASTRUCTURE - SI NVA06A LLC", "TBD COMPASS CREEK PARKWAY".

    DEQ spells the site differently, so the name join misses. EPA's source id
    carries DEQ registration 74269 in Loudoun (FIPS 107), and DEQ lists 74269-2
    in Loudoun Co. Both halves agree, so the permit attaches.
    """
    block = {"name": "STACK INFRASTRUCTURE - SI NVA06A LLC", "state": "VA",
             "class_verified": False, "class_evidence": "none"}
    apply_registry(block, registry, "VA0000005110774269")
    assert block["class_verified"] is True
    assert block["permit_registrations"] == ["74269-2"]
    assert block["class_match_method"] == "exact_registration_number"


def test_a_registration_number_in_the_wrong_county_is_refused(registry: VirginiaRegistry):
    # 74269 is a Loudoun permit; the same digits under Fairfax (059) are not it.
    assert registry.permits_for_source_id("VA0000005105974269") == []
    block = {"name": "SOMEONE ELSE", "state": "VA", "class_verified": False}
    apply_registry(block, registry, "VA0000005105974269")
    assert block["class_verified"] is False


def test_the_number_never_crosses_state_lines(registry: VirginiaRegistry):
    block = {"name": "SOMEONE ELSE", "state": "MD", "class_verified": False}
    apply_registry(block, registry, "VA0000005110774269")
    assert block["class_verified"] is False


def test_a_name_match_keeps_its_evidence_when_the_number_also_fits(registry: VirginiaRegistry):
    block = {"name": "VIENNA CLOUD", "state": "VA", "class_verified": False}
    apply_registry(block, registry, "VA0000005110774269")
    assert block["class_match_method"] == "exact_normalised_name"
    assert "74269-2" not in block["permit_registrations"]


def test_every_registration_match_agrees_with_the_name_join_where_both_exist(registry: VirginiaRegistry):
    """The evidence for trusting the number.

    Where an ECHO row's name already matches DEQ exactly and its source id also
    carries a registration number, the two joins must name the same permit.
    If they ever disagree, the number is not the key we think it is.
    """
    import csv as _csv
    echo = CSV.parent / "echo_datacenters_naics518210.csv"
    both = 0
    for row in _csv.DictReader(echo.open()):
        if row["AIRState"] != "VA":
            continue
        by_name = {p["registration"] for p in registry.permits_for(row["AIRName"])}
        by_number = {p["registration"] for p in registry.permits_for_source_id(row["SourceID"])}
        if by_name and by_number:
            both += 1
            assert by_name & by_number, (row["AIRName"], by_name, by_number)
    assert both >= 70, both
