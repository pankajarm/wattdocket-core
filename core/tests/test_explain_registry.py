"""Public-record regressions and adversarial scope/provenance mutations."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from wattdocket.production.explain_registry import build_explain_registry


ROOT = Path(__file__).parent / "fixtures" / "explain_registry"
META = json.loads((ROOT / "fixture.json").read_text())


@pytest.fixture
def files():
    return {str(p.relative_to(ROOT)): p.read_bytes() for p in ROOT.rglob("*.json") if p.name != "fixture.json"}


def build(files):
    return build_explain_registry(files, release_id=META["release_id"], built_at=META["built_at"])[0]


def mutate(files, path, fn):
    obj = json.loads(files[path])
    fn(obj)
    files[path] = json.dumps(obj).encode()


def facts(registry, key, kind=None):
    items = registry["subjects"][key]["facts"]
    return [f for f in items if kind is None or f["kind"] == kind]


def test_real_permit_keeps_archive_legal_scope_and_all_useful_quantities(files):
    registry = build(files)
    items = facts(registry, "facility:echo:VA0000005110774287")
    permit = [f for f in items if f["kind"] == "permit_condition"]
    assert {f["numbers"][0]["value"] for f in permit} == {29, 500, 685325}
    for f in permit:
        assert "current effectiveness is unknown" in f["text"]
        assert "not actual use" in f["text"] and "archived" in f["text"]
        assert f["evidence"][0]["retrieved_at"] is None
        assert f["evidence"][0]["archived_by_at"] == "2026-10-28T06:05:40Z"
        assert f["evidence"][0]["representation"] == "reader_text"
        if f["numbers"][0]["unit"] == "hours/year":
            assert "each named engine" in f["required_qualifiers"]
    equipment = next(f for f in items if f["kind"] == "equipment_nameplate")
    assert equipment["numbers"][0]["value"] == 60.5
    assert equipment["numbers"][0]["unit"] == "MW nameplate"
    assert "not a whole-site total" in equipment["text"]


def test_tx2_reference_scenario_is_substantive_but_never_a_measurement(files):
    registry = build(files)
    s = facts(registry, "facility:echo:110072155814", "scenario")[0]
    assert "56,940 MWh/year" in s["text"]
    assert "about 19,025 t CO2e/yr" in s["text"]
    assert "2023" in s["required_qualifiers"]
    assert "not a facility measurement" in s["required_qualifiers"]
    n = next(n for n in s["numbers"] if n["label"] == "scenario emissions")
    assert n["value"] != 19025 and n["display"] == "19,025" and n["rounded"] is True
    assert registry["subjects"]["facility:echo:110072155814"]["fallback"]["fact_ids"][0] == "reference-scenario"


@pytest.mark.parametrize("change", ["measurement", "map", "factor_hash", "wrong_math"])
def test_scenario_requires_exact_factor_and_safe_binding(files, change):
    def update(cards):
        c = cards["echo_110072155814"]
        s = next(x for x in c["claims"] if x["id"] == "scenario-carbon")
        if change == "measurement": s["inputs"]["facility_measurement"] = True
        if change == "map": c["facility"]["location_quality"]["map_eligible"] = False
        if change == "factor_hash": s["citations"][0]["doc_sha256"] = "0" * 64
        if change == "wrong_math": s["value"] *= 10
    mutate(files, "cards.json", update)
    assert not facts(build(files), "facility:echo:110072155814", "scenario")


@pytest.mark.parametrize("change", ["name_only", "wrong_registration", "wrong_county", "label_only"])
def test_scout_style_name_join_or_unverified_text_cannot_supply_permits(files, change):
    def update(cards):
        c = cards["echo_va0000005110774287"]
        f = c["facility"]
        if change == "name_only": f["identity_match"]["method"] = "exact_name"
        if change == "wrong_registration": f["identity_match"]["matched_source_ids"] = ["VA0000005110774268"]
        if change == "wrong_county":
            for p in f["permit_limits"]: p["county_fips"] = "153"
        if change == "label_only":
            for p in f["permit_limits"]: p["extraction_version"] = None
    mutate(files, "cards.json", update)
    assert not facts(build(files), "facility:echo:VA0000005110774287", "permit_condition")


def test_linked_annual_totals_stay_on_original_building_scope(files):
    registry = build(files)
    linked = [f for s in registry["subjects"].values() for f in s["facts"] if f["kind"] == "linked_annual_disclosure"]
    assert linked
    for f in linked:
        assert f["scope"] == "building"
        assert "not allocated to this inventory record" in f["required_qualifiers"]
        assert f["related_subject"] in registry["subjects"]
        assert len(f["evidence"]) >= 3


@pytest.mark.parametrize("value", [0, 12.6, "120 MW", False])
def test_new_actual_measurement_cannot_inherit_a_false_missing_claim(files, value):
    def update(cards):
        c = cards["echo_110072155814"]
        next(x for x in c["claims"] if x["id"] == "power-mw")["value"] = value
    mutate(files, "cards.json", update)
    with pytest.raises(ValueError, match="cannot assert a gap"):
        build(files)


def test_shared_units_do_not_allow_water_metric_substitution(files):
    registry = build(files)
    water_facts = [f for s in registry["subjects"].values() for f in s["facts"]
                   if any(n.get("label") == "water_withdrawal" for n in f["numbers"])]
    assert water_facts
    assert all("water withdrawal" in f["required_qualifiers"] for f in water_facts)


def test_permit_purpose_substitution_is_rejected_by_response_validator(files, monkeypatch):
    # Exercise the independently owned response gate with real document facts:
    # 29 and 500 share hours/year but govern different operating purposes.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "api" / "lambda_explain"))
    from response_validation import InvalidExplanation, validate_model_response
    items = facts(build(files), "facility:echo:VA0000005110774287", "permit_condition")
    maintenance = next(f for f in items if f["numbers"][0]["value"] == 29)
    operating = next(f for f in items if f["numbers"][0]["value"] == 500)
    text = maintenance["text"].replace("scheduled maintenance and readiness-testing condition", "all-purpose operating-hours condition")
    raw = json.dumps({"paragraphs": [{"text": text, "fact_ids": [maintenance["id"]]},
                                     {"text": operating["text"], "fact_ids": [operating["id"]]}]})
    with pytest.raises(InvalidExplanation, match="MISSING_SCOPE_QUALIFIER"):
        validate_model_response(raw, [maintenance, operating])


@pytest.mark.parametrize("change", ["wrong_hash", "missing_receipt", "wrong_url", "wrong_year", "allocated"])
def test_annual_label_alone_never_establishes_numeric_evidence(files, change):
    def update(data):
        for l in data["locations"]:
            for o in l["observations"]:
                if change == "wrong_hash": o["source_sha256"] = "0" * 64
                if change == "missing_receipt": o.pop("source_receipt", None)
                if change == "wrong_url": o["source_url"] = "https://example.org/unrelated"
                if change == "wrong_year": o["data_year"] += 1
                if change == "allocated": o["inventory_allocation"] = True
    mutate(files, "facility-observations.json", update)
    registry = build(files)
    assert not [f for s in registry["subjects"].values() for f in s["facts"] if "annual_disclosure" in f["kind"]]
    assert registry["coverage"]["explain_withheld"]["annual_observations_without_eligible_evidence"] > 0


def test_power_quantities_preserve_native_units_boundary_and_exact_times(files):
    registry = build(files)
    ne = facts(registry, "power_site:ne-dcr:106518", "reported_peak_demand")[0]
    assert ne["numbers"][0]["value"] == 254.5 and ne["numbers"][0]["unit"] == "MW"
    assert "2025" in ne["required_qualifiers"] and "campus maximum" in ne["required_qualifiers"]
    ny = facts(registry, "power_site:nyc:21355509", "reported_peak_demand")[0]
    assert ny["numbers"][0]["value"] == 12.6 and ny["numbers"][0]["unit"] == "kW"
    assert "largest individual meter entry" in ny["required_qualifiers"]
    assert "not a whole-property peak" in ny["required_qualifiers"]
    nrel = facts(registry, "power_site:nlr:esif-hpc-golden-co", "historical_power_series")[0]
    assert "2025-08-29T04:00:00Z" in nrel["text"]
    assert "IT equipment" in nrel["required_qualifiers"] and "not a billing peak" in nrel["required_qualifiers"]
    assert nrel["evidence"][0]["publisher"] == "DOE/NREL/ALLIANCE"
    assert not [f for s in registry["subjects"].values() if s["kind"] == "facility" for f in s["facts"] if f["kind"] == "reported_peak_demand"]


def test_power_scope_joins_are_explicitly_rejected(files):
    mutate(files, "facility-power.json", lambda p: p["sites"][0].update(inventory_record_ids=["echo:123"]))
    with pytest.raises(ValueError, match="unjoined"):
        build(files)


def test_bad_power_identity_receipt_withholds_numbers(files):
    mutate(files, "facility-power.json", lambda p: [e.update(sha256="0" * 64) for s in p["sites"] for e in s["identity_evidence"]])
    registry = build(files)
    assert all(not f["numbers"] for s in registry["subjects"].values() if s["kind"] == "power_site" for f in s["facts"])


@pytest.mark.parametrize("path", ["cards.json", "facility-observations.json", "facility-power.json"])
def test_cross_release_data_cannot_enter_registry(files, path):
    mutate(files, path, lambda obj: (next(iter(obj.values())) if path == "cards.json" else obj).update(release_id="different-release"))
    with pytest.raises(ValueError, match="release"):
        build(files)


def test_fallback_exact_cited_facts_aliases_and_release_hashes(files):
    registry = build(files)
    assert registry == build(copy.deepcopy(files))
    assert registry["aliases"]["echo_110072155814"] == "facility:echo:110072155814"
    for key, subject in registry["subjects"].items():
        assert key == subject["kind"] + ":" + subject["id"]
        byid = {f["id"]: f for f in subject["facts"]}
        assert 1 <= len(byid) <= 12
        assert subject["fallback"]["text"] == " ".join(byid[f]["text"] for f in subject["fallback"]["fact_ids"])
        for f in subject["facts"]:
            assert len(f["text"]) <= 900
            assert all(q in f["text"] for q in f["required_qualifiers"])
            assert all(n["display"] in f["text"] for n in f["numbers"])
            for e in f["evidence"]:
                assert e["url"].startswith("https://") and len(e["sha256"]) == 64 and e["locator"]
                prefix = f"https://wattdocket.com/data/releases/{META['release_id']}/"
                if e["url"].startswith(prefix):
                    assert hashlib.sha256(files[e["url"][len(prefix):]]).hexdigest() == e["sha256"]
