"""Independent release acceptance must catch mutated quantities and boundaries."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from wattdocket.production.explain_registry import build_explain_registry

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("explain_release_verifier", ROOT / "scripts/verify_production_release.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.fixture
def release():
    folder = Path(__file__).parent / "fixtures/explain_registry"
    metadata = json.loads((folder / "fixture.json").read_bytes())
    raw = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*.json") if p.name != "fixture.json"}
    registry, coverage = build_explain_registry(raw, release_id=metadata["release_id"], built_at=metadata["built_at"])
    objects = {path: json.loads(value) for path, value in raw.items()}
    objects["explain-registry.json"] = registry
    return objects, raw, {**metadata, "coverage": coverage}


def check(release):
    checks = verifier.Checks()
    verifier.validate_explain_registry(*release, checks)
    return checks


def fact(release, category):
    return next(f for s in release[0]["explain-registry.json"]["subjects"].values() for f in s["facts"] if f["kind"] == category)


def test_current_public_fixtures_reconcile_without_builder_in_verifier(release):
    result = check(release)
    assert result.failures == 0, result.errors
    assert result.count > 200


@pytest.mark.parametrize("category", ["annual_disclosure", "linked_annual_disclosure", "permit_condition", "equipment_nameplate", "scenario", "reported_peak_demand", "historical_power_series", "series_completeness"])
def test_changed_registry_quantity_rejected_against_unchanged_source(release, category):
    fact(release, category)["numbers"][0]["value"] += 1
    assert check(release).failures > 0


@pytest.mark.parametrize("mutation", ["wrong_release", "missing_subject", "other_site", "source_hash", "year", "fallback", "permit_effectiveness", "selector", "scope"])
def test_changed_identity_source_period_or_boundary_is_rejected(release, mutation):
    registry = release[0]["explain-registry.json"]
    if mutation == "wrong_release": registry["release_id"] = "newer-release"
    elif mutation == "missing_subject": registry["subjects"].pop(next(iter(registry["subjects"])))
    elif mutation == "other_site":
        subject = next(s for s in registry["subjects"].values() if s["kind"] == "power_site")
        subject["id"] = "some-other-site"
    elif mutation == "source_hash": fact(release, "annual_disclosure")["evidence"][0]["sha256"] = "f" * 64
    elif mutation == "year": fact(release, "annual_disclosure")["period"]["year"] += 1
    elif mutation == "fallback": next(iter(registry["subjects"].values()))["fallback"]["text"] = "Invented source summary."
    elif mutation == "permit_effectiveness": fact(release, "permit_condition")["period"]["current_effectiveness_verified"] = True
    elif mutation == "selector": fact(release, "annual_disclosure")["source_selector"]["observation_id"] = "another-observation"
    else: fact(release, "annual_disclosure")["scope"] = "whole_facility"
    assert check(release).failures > 0
