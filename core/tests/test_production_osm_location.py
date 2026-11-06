"""Area centers must not masquerade as site points, including retained states."""
import copy
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

from wattdocket.production import pipeline
from wattdocket.production.build import build_release
from wattdocket.production.fetch import stamp
from wattdocket.production.identity import review_facilities
from wattdocket.production.models import SourceResult
from wattdocket.production.quality import RecordEvidenceError, validate_record_evidence
from wattdocket.production.sources_facilities import normalize_osm
from wattdocket.production.storage import LocalStore, digest, json_bytes


NOW = datetime(2026, 11, 3, 22, tzinfo=timezone.utc)


def osm_rows():
    payload = {"osm3s": {"timestamp_osm_base": "2026-11-03T20:00:00Z"}, "elements": [
        {"type": kind, "id": i, **({"lat": 2, "lon": 2} if kind == "node" else {"center": {"lat": 2, "lon": 2}}),
         "tags": {"telecom": "data_center", "name": kind + " example"}} for i, kind in enumerate(("node", "way", "relation"), 1)]}
    return normalize_osm(payload)


def source_state(sid, data, count, *, retrieved=NOW):
    result = SourceResult(sid, sid, "https://example.test/" + sid, data, count).as_dict()
    return {"id": sid, "label": sid, "status": "current", "result": result, "record_count": count,
            "result_sha256": digest(json_bytes(result)), "retrieved_at": stamp(retrieved),
            "last_success_at": stamp(retrieved), "last_attempt_at": stamp(retrieved), "error": None}


def build_states(rows):
    shape = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"subregion": "TEST", "geometry_valid": True},
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}}]}
    return {"echo": source_state("echo", {"facilities": []}, 0),
            "osm": source_state("osm", {"facilities": rows}, len(rows), retrieved=NOW - timedelta(days=1)),
            "egrid": source_state("egrid", {"rates": {"TEST": {"name": "Test", "data_year": 2023,
                "co2e_rate_lb_per_mwh": 100, "workbook_sha256": "a" * 64}}}, 1),
            "egrid_geometry": source_state("egrid_geometry", {"geojson": shape,
                "overlap_geojson": {"type": "FeatureCollection", "features": []}, "data_year": 2023}, 1)}


def test_fresh_node_and_area_coordinates_retain_different_provenance():
    rows = osm_rows()
    reviews = review_facilities(rows)
    for row in rows:
        node = row["osm_type"] == "node"
        assert row["coordinate_source"] == ("osm_node" if node else "osm_bounding_box_center")
        assert row["coordinate_derivation_basis"] == "publisher_element_type"
        quality = reviews[row["id"]]["location_quality"]
        assert quality["map_eligible"] is node
        assert quality["physical_site_verified"] is False
        assert quality["evidence"]["reported_accuracy_m"] is None


def test_concave_polygon_center_can_be_outside_and_is_not_a_site_pin():
    concave = Polygon([(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4), (0, 0)])
    center = Point(2, 2)
    assert not concave.covers(center)
    for row in osm_rows()[1:]:
        quality = review_facilities([row])[row["id"]]["location_quality"]
        assert not quality["map_eligible"]
        assert "osm_bounding_box_center" in quality["reason_codes"]


@pytest.mark.parametrize("keep_type", [True, False])
def test_retained_rows_infer_coordinate_derivation_without_rewriting_source_dates(keep_type, tmp_path):
    rows = osm_rows()
    for row in rows:
        for field in ("coordinate_source", "coordinate_quality", "coordinate_accuracy_m", "coordinate_derivation_basis"):
            row.pop(field)
        if not keep_type:
            row.pop("osm_type")
    states = build_states(rows)
    before = copy.deepcopy(states["osm"])
    archive = LocalStore(tmp_path)
    archive.put("state/sources/osm.json", json_bytes(before))
    class Fetcher:
        def __init__(self, *args): self.receipts = {}
    def unavailable(*args, **kwargs): raise TimeoutError("publisher unavailable")
    retained = pipeline.collect_one("osm", unavailable, archive, NOW, force=True, fetcher_factory=Fetcher)
    assert retained["result"] == before["result"]
    assert retained["result_sha256"] == before["result_sha256"]
    assert retained["retrieved_at"] == retained["last_success_at"] == before["retrieved_at"]
    assert retained["status"] == "stale"
    states["osm"] = retained
    files, coverage = build_release(states, "test", stamp(NOW))
    cards = json.loads(files["cards.json"])
    assert coverage["map_eligible_records"] == coverage["with_grid_binding"] == 1
    for key, card in cards.items():
        quality = card["facility"]["location_quality"]
        assert quality["evidence"]["coordinate_derivation_basis"] == (
            "inferred_from_retained_osm_type" if keep_type else "inferred_from_retained_source_id")
        assert next(s for s in card["data_status"]["sources"] if s["id"] == "osm")["retrieved_at"] == before["retrieved_at"]
        if "node" not in key:
            assert not quality["map_eligible"] and card["grid"]["subregion"] is None
            assert card["grid"]["rate_lb_per_mwh"] is None and "alternative" not in card["grid"]
            assert not any(c["id"].startswith("scenario-") for c in card["claims"])


def test_retained_type_conflict_cannot_turn_an_area_into_a_node():
    row = {"id": "osm:way:123", "osm_type": "node", "lat": 2, "lon": 2}
    quality = review_facilities([row])[row["id"]]["location_quality"]
    assert not quality["map_eligible"]
    assert "osm_element_type_conflict" in quality["reason_codes"]


@pytest.mark.parametrize("mutation", ["map", "grid", "rate", "alternative", "scenario", "provenance"])
def test_independent_gates_reject_tampered_area_scope_even_if_quality_claims_safe(mutation):
    files, _ = build_release(build_states(osm_rows()), "test", stamp(NOW))
    cards, entries = json.loads(files["cards.json"]), json.loads(files["facilities.json"])["results"]
    card = cards["osm_way_2"]
    quality = card["facility"]["location_quality"]
    if mutation == "map": quality["map_eligible"] = True
    elif mutation == "grid": card["grid"]["subregion"] = "TEST"
    elif mutation == "rate": card["grid"]["rate_lb_per_mwh"] = 100
    elif mutation == "alternative": card["grid"]["alternative"] = {"data_year": 2024}
    elif mutation == "scenario": card["claims"].append({"id": "scenario-power", "scope": "scenario"})
    else: quality["evidence"]["coordinate_source"] = "osm_node"
    next(e for e in entries if e["id"] == "osm:way:2")["location_quality"] = copy.deepcopy(quality)
    with pytest.raises(RecordEvidenceError, match="OSM"):
        validate_record_evidence(cards, entries)
    path = Path(__file__).resolve().parents[2] / "scripts/verify_production_release.py"
    spec = importlib.util.spec_from_file_location("independent_verifier", path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    checks = verifier.Checks()
    verifier.check_osm_coordinate_scope("osm:way:2", quality, card["grid"], card["claims"], checks)
    assert checks.failures > 0
