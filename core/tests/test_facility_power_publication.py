"""Tamper the emitted public contract, independently of ingestion/build helpers."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("independent_power_verifier", ROOT / "scripts/verify_facility_power.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
FIXTURE = Path(__file__).parent / "fixtures/facility_power_publication.json"


class Checks:
    def __init__(self):
        self.count, self.errors = 0, []

    def require(self, condition, message):
        self.count += 1
        if not condition:
            self.errors.append(message)
        return bool(condition)


def sample():
    # Real captured source/proof metadata and sample windows; row/count metadata
    # reduced together for a bounded public-object regression fixture.
    objects = json.loads(FIXTURE.read_text())
    return objects, deepcopy(objects["facility-power.json"]["coverage"])


def verify(objects, coverage):
    checks = Checks()
    paths = VERIFIER.validate_facility_power(objects, coverage, checks)
    return checks, paths


def series(objects, source="inl_c3_power"):
    descriptor = next(row for row in objects["facility-power.json"]["series"] if row["source_id"] == source)
    return descriptor, objects[descriptor["readings_path"]]


def sync_descriptor(descriptor, item):
    descriptor.update({k: v for k, v in item.items() if k != "readings"})


def test_actual_source_contracts_and_original_cached_receipts_pass():
    objects, coverage = sample()
    checks, paths = verify(objects, coverage)
    assert checks.errors == []
    assert paths == {"facility-power.json", "power/inl-c3-hpc-meter-power.json", "power/nlr-esif-it-power-hourly.json"}
    assert coverage["live_facility_meter_series"] == 0
    assert objects["facility-power.json"]["series"][0]["retrieved_at"] != objects["facility-power.json"]["series"][0]["checked_at"]


@pytest.mark.parametrize("damage", [
    "descriptor", "columns", "count", "missing", "unit", "scope", "live", "release", "source_count",
    "receipt_hash", "receipt_url", "receipt_key", "retrieval", "missing_object", "extra_object", "coverage",
    "duplicate_time", "clock_shift", "null_to_zero", "numeric_boolean", "negative", "nonfinite",
])
def test_interval_and_descriptor_tampering_is_rejected(damage):
    objects, coverage = sample()
    descriptor, item = series(objects)
    if damage == "descriptor":
        descriptor["maximum_reported_kw"] += 1
    elif damage == "columns":
        item["columns"] = ["timestamp_utc", "energy_kwh", *item["columns"][2:]]
    elif damage == "count":
        item["measured_count"] += 1
    elif damage == "missing":
        item["missing_count"] = 0
    elif damage == "unit":
        item["unit"] = "kWh"
    elif damage == "scope":
        item["whole_facility"] = True
    elif damage == "live":
        item["historical_snapshot"] = False
    elif damage == "release":
        item["release_id"] = "other-release"
    elif damage == "source_count":
        next(s for s in objects["data-status.json"]["sources"] if s["id"] == "inl_c3_power")["record_count"] += 1
    elif damage.startswith("receipt_"):
        key = {"receipt_hash": "sha256", "receipt_url": "url", "receipt_key": "raw_key"}[damage]
        item["source_receipt"][key] = "wrong"
    elif damage == "retrieval":
        item["retrieved_at"] = item["checked_at"]
    elif damage == "missing_object":
        del objects[descriptor["readings_path"]]
    elif damage == "extra_object":
        objects["power/orphan.json"] = deepcopy(item)
    elif damage == "coverage":
        coverage["power_interval_readings"] += 1
    elif damage == "duplicate_time":
        item["readings"][1][0] = item["readings"][0][0]
    elif damage == "clock_shift":
        item["readings"][0][4] = "MDT" if item["readings"][0][4] == "MST" else "MST"
    elif damage == "null_to_zero":
        next(row for row in item["readings"] if row[1] is None)[1] = 0
    else:
        item["readings"][0][1] = {"numeric_boolean": True, "negative": -1, "nonfinite": float("inf")}[damage]
    # Most attacks alter both descriptor and object: descriptor equality alone
    # must not be the reason invalid facts or scope changes are rejected.
    if damage != "descriptor":
        sync_descriptor(descriptor, item)
    checks, _ = verify(objects, coverage)
    assert checks.errors, damage


@pytest.mark.parametrize("damage", ["mean", "few_samples", "sample_gap", "duplicate_samples", "missing_method_receipt", "license_truncated", "license_receipt"])
def test_derived_esif_means_need_reconcilable_samples_and_method_evidence(damage):
    objects, coverage = sample()
    descriptor, item = series(objects, "nrel_esif_power")
    row = next(row for row in item["readings"] if row[1] is not None)
    if damage == "mean":
        row[1] += 1
        values = [r[1] for r in item["readings"] if r[1] is not None]
        item.update(maximum_reported_kw=max(values), minimum_reported_kw=min(values))
    elif damage == "few_samples":
        row[5] = 44
    elif damage == "sample_gap":
        row[6] = 301
    elif damage == "duplicate_samples":
        row[8] = 1
    elif damage == "missing_method_receipt":
        item["method_evidence"][0].pop("receipt")
    elif damage == "license_truncated":
        item["license"]["notice"] = "Some permission"
    else:
        item["license"]["receipt"]["sha256"] = "0" * 64
    sync_descriptor(descriptor, item)
    checks, _ = verify(objects, coverage)
    assert checks.errors, damage


@pytest.mark.parametrize("damage", ["allocated", "street", "proof", "proof_receipt", "orphan_site"])
def test_physical_site_proof_cannot_be_replaced_by_unverified_inventory_match(damage):
    objects, coverage = sample()
    site = next(s for s in objects["facility-power.json"]["sites"] if s["site_id"] == "inl-c3")
    if damage == "allocated":
        site["inventory_record_ids"] = ["echo:110000000000"]
    elif damage == "street":
        site["address"]["street"] = "Wrong physical street"
    elif damage == "proof":
        site["identity_evidence"] = []
    elif damage == "proof_receipt":
        site["identity_evidence"][0]["receipt"]["url"] = "https://wrong.example/proof.pdf"
    else:
        duplicate = deepcopy(site)
        duplicate["site_id"] = "unreferenced-copy"
        objects["facility-power.json"]["sites"].append(duplicate)
    checks, _ = verify(objects, coverage)
    assert checks.errors, damage


@pytest.mark.parametrize("damage", ["zero", "negative", "boolean", "year", "scope", "meter", "month", "native_value", "period", "interval", "allocated"])
def test_nyc_annual_single_meter_peak_cannot_become_facility_demand(damage):
    objects, coverage = sample()
    demand = next(d for d in objects["facility-power.json"]["demands"] if d["source_id"] == "nyc_facility_demand")
    changes = {"zero": ("value", 0), "negative": ("value", -1), "boolean": ("value", True),
               "year": ("reporting_year", 2026), "scope": ("whole_facility", True),
               "meter": ("publisher_meter_id", "invented"), "month": ("peak_month", "2024-13"),
               "native_value": ("value", demand["value"] + 1), "period": ("period_end", "2027-01-01T00:00:00Z"),
               "interval": ("is_meter_observation", True), "allocated": ("inventory_allocation", True)}
    key, value = changes[damage]
    demand[key] = value
    checks, _ = verify(objects, coverage)
    assert checks.errors, damage


@pytest.mark.parametrize("damage", ["number", "hash", "return_date", "redaction", "peak_month"])
def test_nebraska_reviewed_visible_peak_keeps_document_and_public_return_evidence(damage):
    objects, coverage = sample()
    demand = next(d for d in objects["facility-power.json"]["demands"] if d["site_id"] == "ne-dcr:90045")
    if damage == "number":
        demand["value"] = 526.5
    elif damage == "hash":
        demand["source_sha256"] = "a" * 64
        demand["source_receipt"].update(sha256="a" * 64, raw_key=f"raw/{'a' * 64}.gz")
    elif damage == "return_date":
        demand["public_access_evidence"]["returned_to_public_file_at"] = None
    elif damage == "redaction":
        demand["public_access_evidence"]["redacted_values_used"] = True
    else:
        demand["peak_month"] = "2025-01"
    checks, _ = verify(objects, coverage)
    assert checks.errors, damage


@pytest.mark.parametrize("field,value", [("alert_energy_meter_has_gaps", "Alert"),
                                        ("construction_status", "New construction"),
                                        ("metered_areas_energy", "Partial Property")])
def test_nyc_missing_source_quality_cannot_be_hidden_by_a_positive_peak(field, value):
    objects, coverage = sample()
    site = next(s for s in objects["facility-power.json"]["sites"] if s["source_id"] == "nyc_facility_demand")
    site["raw_source_row"][field] = value
    checks, _ = verify(objects, coverage)
    assert checks.errors


def test_missing_index_cannot_hide_power_coverage_or_orphaned_series():
    objects, coverage = sample()
    del objects["facility-power.json"]
    checks, _ = verify(objects, coverage)
    assert checks.errors


def test_legacy_release_without_power_objects_or_claimed_coverage_is_valid():
    checks, paths = verify({}, {})
    assert checks.errors == []
    assert paths == set()
