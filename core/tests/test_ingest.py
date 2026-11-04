"""Offline tests for the ingestion lane: frozen inputs in, verified numbers out.

Every test here runs against committed fixtures — no network, no model, no
SQLite server. The two lanes that matter today are:

* **cards** — 466 facility cards and their index, the demo's data path;
* **factor set** — INV-6's link from a PROVISIONAL number to a hashed table.

The ``ledger`` and ``--fixture-dir`` replay paths are exercised too, so the
builder that produced them cannot rot silently.

Fixture provenance lives in ``tests/fixtures/manifest.json``; re-capture with
``python -m wattdocket.ingest.capture_fixtures``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from wattdocket import cards as chair_cards
from wattdocket.grid import DEFAULT_FACTOR_SET, factor_set_hash
from wattdocket.ingest import cards, echo, egrid, ercot, geo, ledger
from wattdocket.ingest.net import FixtureFetcher, IngestionError

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
FIXTURES = TESTS / "fixtures"
DATA = REPO / "data"

ECHO_CSV = DATA / "echo_datacenters_naics518210.csv"
RATES_JSON = DATA / "egrid_subregion_rates.json"
POLYGONS = DATA / "egrid_subregions.geojson"
SCHEMA = REPO / "schemas" / "card.schema.json"
EGRID_WORKBOOK = FIXTURES / "egrid_srl23.xlsx"

# Verified against the live EPA feature service on 2026-10-28 (see geo_queries.json).
CITY_SUBREGIONS = {
    "21715 FILIGREE CT, ASHBURN, VA, 20147": "SRVC",
    "2020 N LAMAR ST, DALLAS, TX, 75202": "ERCT",
    "2455 AUGUSTINE DR, SANTA CLARA, CA, 95054": "CAMX",
    "1 W BROAD ST, COLUMBUS, OH, 43215": "RFCW",
    "200 GENESEE ST, UTICA, NY, 13502": "NYUP",
}

# eGRID 2023 SRL23 CO2e rates, lb/MWh (reference: hackathon/grid/egrid_rates.json).
EXPECTED_RATES = {"ERCT": 736.6, "SRVC": 596.3, "NYUP": 242.8, "RFCW": 916.1,
                  "CAMX": 430.0}


# ----------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def fetcher() -> FixtureFetcher:
    return FixtureFetcher(FIXTURES)


@pytest.fixture(scope="module")
def polygons() -> list[dict]:
    return geo.load_subregion_polygons(POLYGONS)


@pytest.fixture(scope="module")
def frozen_rates() -> dict[str, dict]:
    return egrid.load_frozen_rates(RATES_JSON)


@pytest.fixture(scope="module")
def built_cards(tmp_path_factory) -> dict:
    """One real build of the card set, reused by the read-only tests."""
    out = tmp_path_factory.mktemp("cards")
    result = cards.build_cards(ECHO_CSV, RATES_JSON, POLYGONS, out / "cards",
                               out / "cards.json", out / "calculations.json")
    result["dir"] = out
    return result


def _read_card(built: dict, registry: str) -> dict:
    return json.loads((built["dir"] / "cards" / f"echo_{registry}.json").read_text())


# ------------------------------------------------------------------- eGRID
@pytest.mark.skipif(not EGRID_WORKBOOK.is_file(), reason="eGRID workbook fixture absent")
def test_egrid_parses_27_subregions_with_reference_rates():
    """SRCO2EQA is short tons/yr; the rate is mass x 2000 / generation (INV-1)."""
    rates = egrid.parse_subregion_rates(EGRID_WORKBOOK)
    assert len(rates) == 27
    for code, expected in EXPECTED_RATES.items():
        assert rates[code]["co2e_rate_lb_per_mwh"] == pytest.approx(expected, abs=0.5), code
    # Recomputed independently from the workbook's own columns.
    for code, row in rates.items():
        recomputed = row["co2e_short_tons_annual"] * 2000.0 / row["gen_mwh"]
        assert row["co2e_rate_lb_per_mwh"] == pytest.approx(recomputed, rel=1e-9), code
        assert 50.0 <= row["co2e_rate_lb_per_mwh"] <= 2000.0, code
    # Metric tonnes must be *lighter* than the short tons eGRID published.
    assert rates["ERCT"]["co2_metric_tonnes_annual"] < \
        rates["ERCT"]["co2_short_tons_annual"]


@pytest.mark.skipif(not EGRID_WORKBOOK.is_file(), reason="eGRID workbook fixture absent")
def test_frozen_rates_file_matches_the_workbook(frozen_rates):
    """The committed rates are the workbook's, not a hand-typed table."""
    parsed = egrid.parse_subregion_rates(EGRID_WORKBOOK)
    assert set(frozen_rates) == set(parsed)
    for code, row in parsed.items():
        assert frozen_rates[code]["co2e_rate_lb_per_mwh"] == pytest.approx(
            row["co2e_rate_lb_per_mwh"], abs=0.001), code


def test_frozen_rates_reject_a_misscaled_rate(tmp_path):
    """A short-ton/metric-tonne mixup lands outside the physical gate."""
    payload = json.loads(RATES_JSON.read_text())
    payload["subregions"]["ERCT"]["co2e_rate_lb_per_mwh"] = 1.0
    bad = tmp_path / "rates.json"
    bad.write_text(json.dumps(payload))
    with pytest.raises(egrid.EgridError):
        egrid.load_frozen_rates(bad)


# ------------------------------------------------------------ factor set
def test_factor_set_hash_is_real_and_self_consistent():
    """INV-6: the pinned hash is the hash of the table, not a placeholder."""
    assert DEFAULT_FACTOR_SET.sha256 != "0" * 64
    assert factor_set_hash(DEFAULT_FACTOR_SET.lb_per_mwh) == DEFAULT_FACTOR_SET.sha256


def test_factor_set_artifact_matches_the_pinned_hash():
    payload = cards.factor_set_payload()
    assert payload["sha256"] == DEFAULT_FACTOR_SET.sha256
    recomputed = hashlib.sha256(payload["canonical_json"].encode()).hexdigest()
    assert recomputed == payload["sha256"]
    assert payload["lb_per_mwh"] == DEFAULT_FACTOR_SET.lb_per_mwh


# ------------------------------------------------------------------ ECHO
def test_echo_fixture_yields_466_facilities_from_479_source_rows(fetcher):
    """479 air-source rows collapse to 466 facilities; nothing is dropped."""
    payload = fetcher.get_json(echo.ECHO_URL)
    assert len(payload["Results"]["Facilities"]) == 479
    facilities = echo.parse_echo_payload(payload, retrieved_at="2026-10-27T00:00:00Z")
    assert len(facilities) == 466
    assert all(f["lat"] and f["lon"] for f in facilities)
    assert all(f["capacity_basis"] == "UNKNOWN" for f in facilities)
    assert sum(f["source_rows"] for f in facilities) == 479
    assert max(f["source_rows"] for f in facilities) == 3


def test_echo_refuses_a_truncated_payload(fetcher):
    """A paged/short response must not become a smaller ledger."""
    payload = fetcher.get_json(echo.ECHO_URL)
    payload["Results"]["Facilities"] = payload["Results"]["Facilities"][:10]
    with pytest.raises(echo.EchoError, match="truncated"):
        echo.parse_echo_payload(payload)


def test_echo_refuses_a_facility_without_coordinates(fetcher):
    payload = fetcher.get_json(echo.ECHO_URL)
    payload["Results"]["Facilities"][0]["FacLat"] = None
    with pytest.raises(echo.EchoError):
        echo.parse_echo_payload(payload)


# ------------------------------------------------------------------ ERCOT
def test_ercot_day_is_utc_keyed_with_storage_excluded(fetcher):
    """INV-2/INV-3/INV-5: UTC keys, explicit label, storage out of the total."""
    payload = fetcher.get_json(ercot.ERCOT_URL)
    rows = ercot.parse_day(payload, day="2026-10-26")
    assert len(rows) == 288                      # 24 h of 5-minute intervals
    assert all(r["interval_start_utc"].endswith("Z") for r in rows)
    assert rows[0]["interval_start_utc"] == "2026-10-26T05:00:00Z"   # 00:00 -0500
    assert rows[0]["label"].endswith("-0500")
    assert rows[0]["hour_convention"] == "START"
    assert all(r["total_gen_mwh"] > 0 for r in rows)
    assert all("storage" not in fuel for r in rows for fuel in r["gen_mwh_by_fuel"])
    # Storage was present in the source and was excluded, not lost: the day
    # contains both charging (negative) and discharging (positive) intervals.
    storage = [r["storage_excluded_mwh"] for r in rows]
    assert any(value < 0 for value in storage)
    assert any(value > 0 for value in storage)
    # Intensity is derived, provisional, and names its factor set (INV-6).
    assert all(r["intensity_state"] == "PROVISIONAL" for r in rows)
    assert all(r["factor_set_id"] == DEFAULT_FACTOR_SET.id for r in rows)
    summary = ercot.day_summary(rows)
    assert summary["min"] < summary["mean"] < summary["max"]
    assert 1.0 < summary["ratio"] < 3.0


def test_ercot_refuses_a_gap(fetcher):
    payload = fetcher.get_json(ercot.ERCOT_URL)
    day = payload["data"]["2026-10-26"]
    del day[sorted(day)[10]]
    with pytest.raises(ercot.ErcotError, match="gap"):
        ercot.parse_day(payload, day="2026-10-26")


# -------------------------------------------------------------------- geo
def test_five_cities_resolve_to_their_subregions(polygons):
    """Point-in-polygon against the bundled eGRID polygons, no network."""
    queries = json.loads((FIXTURES / "geo_queries.json").read_text())
    seen = {}
    for url, body in queries.items():
        if "geocoder" not in url:
            continue
        matches = body["result"]["addressMatches"]
        assert matches, url
        lat = matches[0]["coordinates"]["y"]
        lon = matches[0]["coordinates"]["x"]
        seen[matches[0]["matchedAddress"]] = geo.subregion_for_point_local(lat, lon, polygons)
    assert seen == CITY_SUBREGIONS


def test_pip_agrees_with_the_state_default_table_only_where_it_should(polygons):
    """The coarse state->subregion table is wrong for a large minority of sites.

    This is the measured reason the cards use real polygons: a state average
    puts an Illinois facility on SRMW (1239.8 lb/MWh) instead of RFCW (916.1),
    a 35% carbon error.
    """
    import csv

    rows = list(csv.DictReader(ECHO_CSV.open()))
    disagreements = 0
    for row in rows:
        pip = geo.subregion_for_point_local(float(row["FacLat"]), float(row["FacLong"]),
                                           polygons)
        if pip != chair_cards.STATE_DEFAULT.get(row["AIRState"]):
            disagreements += 1
    assert disagreements == 76


def test_point_outside_every_polygon_is_none_not_a_guess(polygons):
    assert geo.subregion_for_point_local(0.0, 0.0, polygons) is None


# ------------------------------------------------------------------ cards
def test_card_count_and_shape(built_cards):
    assert built_cards["cards"] == 466
    index = json.loads((built_cards["dir"] / "cards.json").read_text())
    assert index["card_count"] == 466
    assert index["counts"]["source_rows"] == 479
    assert index["counts"]["facilities"] == 466
    assert len(index["cards"]) == 466
    assert len(list((built_cards["dir"] / "cards").glob("*.json"))) == 466


def test_every_card_validates_against_the_frozen_ui_schema(built_cards):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    for card in cards.load_cards(built_cards["dir"] / "cards.json"):
        jsonschema.validate(card, schema)


def test_every_claim_is_renderable_and_reasoned(built_cards):
    """INV-7 and INV-8: a number needs a receipt; an absence needs a reason."""
    for card in cards.load_cards(built_cards["dir"] / "cards.json"):
        assert len(card["claims"]) == 5
        metrics = {c["metric"] for c in card["claims"]}
        assert metrics == {"carbon_t", "power_mw", "water_gal", "diesel_mw",
                           "diesel_hours"}
        for claim in card["claims"]:
            assert claim["renderable"], claim["id"]
            value = claim["value"]
            if isinstance(value, dict):
                assert value["state"] == "NOT_PUBLISHED"
                assert value["reason"].strip()
                assert claim["citations"] == []
            else:
                assert isinstance(value, (int, float))
                assert claim["citations"], claim["id"]
                assert any(c["verified"] for c in claim["citations"]), claim["id"]


def test_status_is_unknown_because_the_snapshot_has_no_status_column(built_cards):
    index = json.loads((built_cards["dir"] / "cards.json").read_text())
    assert index["counts"]["status_unknown"] == 466
    assert all(entry["status"] == "unknown" for entry in index["cards"].values())


def test_carbon_claim_is_reproducible_and_names_its_limitations(built_cards):
    card = _read_card(built_cards, "110031223771")
    carbon = next(c for c in card["claims"] if c["metric"] == "carbon_t")
    assert carbon["badge"] == "ESTIMATED"
    assert carbon["provisional"] is True
    assert carbon["range"]["p10"] <= carbon["range"]["p50"] <= carbon["range"]["p90"]
    assert "capacity_basis_unknown" in carbon["drivers"]
    assert carbon["calc_id"]
    calcs = json.loads((built_cards["dir"] / "calculations.json").read_text())
    calc = calcs["calculations"][carbon["calc_id"]]
    assert calc["code_sha256"] == calcs["code_sha256"]
    assert len(calc["code_sha256"]) == 64
    assert calc["inputs"]["capacity_basis"]["value"] == "UNKNOWN"
    assert calc["output"]["annual_tonnes_co2e"] == pytest.approx(
        carbon["range"]["p50"], abs=0.1)
    assert "2204.62" in calc["formula"]      # INV-1 shown verbatim


def test_citation_points_at_the_csv_line_it_quotes(built_cards):
    """The receipt is checkable: open the file at that line and find the quote."""
    lines = ECHO_CSV.read_text().splitlines()
    card = _read_card(built_cards, "110031223771")
    carbon = next(c for c in card["claims"] if c["metric"] == "carbon_t")
    assert len(carbon["citations"]) == 3      # three permitted sources, one facility
    assert [c["line"] for c in carbon["citations"]] == [74, 446, 449]
    for citation in carbon["citations"]:
        assert citation["verified"] is True
        assert citation["quote"] == lines[citation["line"] - 1].strip()
        assert citation["doc_sha256"] == hashlib.sha256(ECHO_CSV.read_bytes()).hexdigest()


def test_grid_block_uses_the_polygon_not_the_state(built_cards):
    """An Illinois site is RFCW by geometry; the state table would say SRMW."""
    card = _read_card(built_cards, "110070005159")     # CyrusOne Aurora, IL
    assert card["facility"]["state"] == "IL"
    assert card["grid"]["subregion"] == "RFCW"
    assert card["grid"]["binding_method"] == "point_in_polygon"
    assert card["grid"]["rate_lb_per_mwh"] == pytest.approx(916.1, abs=0.5)
    assert card["grid"]["factor_set_sha256"] == DEFAULT_FACTOR_SET.sha256


def test_verdict_is_a_bounded_template_with_no_unbound_numerals(built_cards):
    from wattdocket.prose import prose_is_bound

    for card in cards.load_cards(built_cards["dir"] / "cards.json"):
        verdict = card["verdict"]
        assert verdict["source"] == "template"
        assert 0 < len(verdict["sentence"]) <= 240
        payload = {k: v for k, v in card.items() if k != "verdict"}
        assert prose_is_bound(verdict["sentence"], payload), verdict["sentence"]


def test_build_is_deterministic(tmp_path):
    """Same committed inputs, same bytes — no wall clock, no cwd in the output."""
    def build(where: Path) -> str:
        cards.build_cards(ECHO_CSV, RATES_JSON, POLYGONS, where / "cards",
                          where / "cards.json", where / "calculations.json")
        digest = hashlib.sha256()
        for path in sorted(where.rglob("*.json")):
            digest.update(str(path.relative_to(where)).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    assert build(tmp_path / "a") == build(tmp_path / "b")


def test_committed_artifacts_match_a_fresh_build(built_cards):
    """The checked-in cards are what the generator produces today."""
    index = json.loads((REPO / "artifacts" / "cards.json").read_text())
    if index.get("generated_by") != cards.GENERATOR:
        pytest.skip("committed artifacts were produced by another generator")
    fresh = json.loads((built_cards["dir"] / "cards.json").read_text())
    assert index["card_count"] == fresh["card_count"]
    assert index["counts"]["by_subregion"] == fresh["counts"]["by_subregion"]
    assert index["sources"]["echo_csv"]["sha256"] == fresh["sources"]["echo_csv"]["sha256"]


# ----------------------------------------------------------------- ledger
def test_ledger_replays_from_fixtures_offline(tmp_path, fetcher):
    """The SQLite lane still works from captured bytes, with no binding step."""
    out = tmp_path / "ledger.sqlite"
    result = ledger.build(out, fetcher=fetcher, bind=False,
                          retrieved_at="2026-10-27T00:00:00Z",
                          egrid_local=EGRID_WORKBOOK)
    assert result["facility"] == 466
    assert result["interval_reading"] == 288
    assert result["subregion_rate"] == 27
    assert result["factor_set"] == 1
    ok, report = ledger.verify(out, require_binding=False)
    assert ok, report["problems"]
    assert report["counts"]["facility"] == 466
    # Storage is carried, not folded into generation: the day nets to a large
    # positive MWh of excluded battery throughput.
    assert abs(report["storage_excluded_mwh"]) > 1000
    assert not (tmp_path / "ledger.sqlite.tmp").exists()
    assert not (tmp_path / "ledger.sqlite-wal").exists()   # WAL off, by contract


def test_ledger_verify_catches_an_impossible_rate(tmp_path):
    out = tmp_path / "ledger.sqlite"
    ledger.build(out, fetcher=FixtureFetcher(FIXTURES), bind=False,
                 retrieved_at="2026-10-27T00:00:00Z", egrid_local=EGRID_WORKBOOK)
    import sqlite3
    conn = sqlite3.connect(out)
    conn.execute("UPDATE subregion_rate SET co2e_rate_lb_per_mwh = 5000 WHERE subregion = 'ERCT'")
    conn.commit()
    conn.close()
    ok, report = ledger.verify(out)
    assert not ok
    assert any("outside 50-2000" in problem for problem in report["problems"])


# ------------------------------------------------------- failure discipline
def test_missing_source_raises_with_the_url_and_never_returns_empty(tmp_path):
    empty = tmp_path / "fixtures"
    empty.mkdir()
    (empty / "manifest.json").write_text(json.dumps({"entries": []}))
    with pytest.raises(IngestionError) as excinfo:
        echo.load_facilities(FixtureFetcher(empty), csv_fallback=None)
    assert "echodata.epa.gov" in str(excinfo.value)
    assert excinfo.value.url and "air_rest_services" in excinfo.value.url


def test_egrid_missing_workbook_names_the_path(tmp_path):
    with pytest.raises(egrid.EgridError, match="not found"):
        egrid.parse_subregion_rates(tmp_path / "absent.xlsx")
