"""The ten invariants from SPEC.md, one test each.

These tests exist because every one of them caught a real defect in the
chair's own working code during the board rounds. They are not coverage
theatre: each one fails if the corresponding bug is reintroduced.
"""

from datetime import datetime, timezone

import pytest

from wattdocket.claims import (Badge, Citation, Claim, NotPublished,
                               not_reported)
from wattdocket.estimator import (Badge as EstBadge, CapacityBasis,
                                  EstimateInput, cars_equivalent, estimate)
from wattdocket.grid import (DEFAULT_FACTOR_SET, FactorSetMissing,
                             IntensityState, intensity_from_fuel_mix)
from wattdocket.timeutil import (Interval, TimeError, dst_transition_day_length,
                                 hour_ending_to_start, hours_in_utc_day,
                                 parse_offset_iso)
from wattdocket.units import UnitError, lb_to_metric_tonnes, mwh_at_rate_to_tonnes
from wattdocket.water import CoolingType, WaterError, WaterState, modelled_band


# ---------------------------------------------------------------- INV-1
def test_mass_is_metric_tonnes():
    """A short ton is 9.2% lighter than a metric tonne; we only emit metric."""
    assert lb_to_metric_tonnes(2204.6226218487757) == pytest.approx(1.0)
    # the historical bug: 2000 lb -> 1.0
    assert lb_to_metric_tonnes(2000) == pytest.approx(0.90718474, rel=1e-6)
    # 100 MW for a year at eGRID's Ohio rate must land in the 230k range,
    # not the 260k a short-ton implementation produces.
    tonnes = mwh_at_rate_to_tonnes(569_400, 916.1)
    assert 230_000 < tonnes < 242_000
    with pytest.raises(UnitError):
        from wattdocket.units import short_tons
        short_tons(2000)


def test_negative_energy_refused():
    with pytest.raises(UnitError):
        mwh_at_rate_to_tonnes(-1, 500)


# ---------------------------------------------------------------- INV-2
def test_dst_transition_day_is_25_hours():
    """US DST ends 1 Nov 2026 — inside the build week."""
    assert hours_in_utc_day(2026, 11, 1) == 24
    assert dst_transition_day_length(2026, 11, 1) == 25
    assert dst_transition_day_length(2026, 3, 8) == 23


def test_interval_requires_utc():
    with pytest.raises(TimeError):
        Interval(start_utc=datetime(2026, 10, 26, 18, 0), minutes=5,
                 label="local", hour_convention="START")


# ---------------------------------------------------------------- INV-3
def test_hour_labelling_is_explicit():
    """EIA-930 stamps are hour-ENDING; ERCOT keys are interval-STARTING."""
    start = parse_offset_iso("2026-10-26 18:00:00-0500")
    assert start == datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)
    # an hour-ending stamp of 18:00 local covers 17:00->18:00 local
    assert hour_ending_to_start("2026-10-26 18:00:00-0500") == \
        datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc)

    iv = Interval(start_utc=datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc),
                  minutes=5, label="2026-10-26 18:00:00-0500",
                  hour_convention="START")
    assert iv.hour_key_utc() == "2026-10-26T23"
    with pytest.raises(TimeError):
        Interval(start_utc=datetime(2026, 10, 26, tzinfo=timezone.utc),
                 minutes=5, label="x", hour_convention="MIDDLE")


# ---------------------------------------------------------------- INV-4
def test_pue_never_applied_to_grid_basis():
    """Reported whole-facility MW already includes cooling."""
    grid = estimate(EstimateInput(capacity_mw=100, capacity_basis=CapacityBasis.GRID_MW,
                                  utilization=0.65, grid_rate_lb_per_mwh=593.4))
    assert grid.assumptions["pue"] == 1.0
    assert grid.assumptions["pue_source"] == "pue_not_applicable"

    with pytest.raises(UnitError):
        estimate(EstimateInput(capacity_mw=100, capacity_basis=CapacityBasis.GRID_MW,
                               utilization=0.65, pue=1.25,
                               grid_rate_lb_per_mwh=593.4))

    it = estimate(EstimateInput(capacity_mw=100, capacity_basis=CapacityBasis.IT_MW,
                                utilization=0.65, pue=1.25,
                                grid_rate_lb_per_mwh=593.4))
    assert it.assumptions["pue"] == 1.25
    # the IT-basis case is exactly 25% larger for the same stated capacity
    assert it.annual_mwh == pytest.approx(grid.annual_mwh * 1.25)


# ---------------------------------------------------------------- INV-5
def test_storage_excluded_from_denominator():
    """Charging is negative MW; summing it shrinks the denominator."""
    with_storage = intensity_from_fuel_mix({
        "Coal and Lignite": 5798.0, "Natural Gas": 20224.0, "Wind": 19603.0,
        "Nuclear": 3850.0, "Solar": 0.4, "Power Storage": -4780.0,
        "Power Storage Charging": -4780.0, "Power Storage Discharging": 0.0,
    })
    without = intensity_from_fuel_mix({
        "Coal and Lignite": 5798.0, "Natural Gas": 20224.0, "Wind": 19603.0,
        "Nuclear": 3850.0, "Solar": 0.4,
    })
    assert with_storage.value_lb_per_mwh == pytest.approx(
        without.value_lb_per_mwh)
    # The net figure and the charging component carry the same MW on real ERCOT
    # data (both -4,780 at 2026-10-26 00:00), so adding both double-counted the
    # same energy and made the daily total 24x too large once the missing 5/60
    # factor was included. Components win; the net aggregate is ignored.
    assert with_storage.storage_excluded_mwh == pytest.approx(-4780.0)


def test_storage_net_figure_used_when_components_absent():
    """A feed that publishes only the net figure still excludes it once."""
    it = intensity_from_fuel_mix({
        "Coal and Lignite": 5000.0, "Natural Gas": 10000.0,
        "Power Storage": -500.0,
    })
    assert it.storage_excluded_mwh == pytest.approx(-500.0)


def test_negative_non_storage_generation_refused():
    with pytest.raises(UnitError):
        intensity_from_fuel_mix({"Coal and Lignite": -10.0, "Wind": 100.0})


# ---------------------------------------------------------------- INV-6
def test_provisional_requires_factor_link():
    i = intensity_from_fuel_mix({"Coal and Lignite": 100.0, "Wind": 100.0})
    assert i.state is IntensityState.PROVISIONAL
    assert i.factor_set_id == DEFAULT_FACTOR_SET.id
    with pytest.raises(FactorSetMissing):
        intensity_from_fuel_mix({"Coal and Lignite": 100.0}, factor_set=None)


def test_unknown_fuel_refuses_instead_of_guessing():
    with pytest.raises(FactorSetMissing):
        intensity_from_fuel_mix({"Geothermal": 50.0, "Wind": 50.0})


# ---------------------------------------------------------------- INV-7
PAGE = """
Minor Modification No. 4, effective 14 June 2021.

Section B.3  The permittee shall not discharge more than 1.20 million gallons
per day from Outfall 001, measured as a monthly average.
"""


def test_claim_without_receipt_cannot_render():
    bare = Claim(id="c1", metric="diesel_mw", value=6.2, unit="MW",
                 badge=Badge.REPORTED, as_of_utc="2026-10-26")
    ok, why = bare.renderable()
    assert not ok and "no citation" in why

    cit = Citation(doc_sha256="abc", page=14, line=3,
                   quote="shall not discharge more than 1.20 million gallons",
                   verified=False).verify(PAGE)
    assert cit.verified, "the quote appears on the page"

    good = Claim(id="c2", metric="limit_mgd", value=1.20, unit="MGD",
                 badge=Badge.REPORTED, as_of_utc="2026-10-26", citations=(cit,))
    assert good.renderable()[0]

    lying = Citation(doc_sha256="abc", page=14, line=3,
                     quote="shall not discharge more than 1.50 million gallons",
                     verified=False).verify(PAGE)
    assert not lying.verified
    bad = Claim(id="c3", metric="limit_mgd", value=1.50, unit="MGD",
                badge=Badge.REPORTED, as_of_utc="2026-10-26", citations=(lying,))
    assert not bad.renderable()[0]


def test_modelled_claim_needs_a_calc_id():
    no_calc = Claim(id="c4", metric="carbon", value=1.0, unit="t",
                    badge=Badge.MODELED, as_of_utc="2026-10-26")
    assert not no_calc.renderable()[0]
    with_calc = Claim(id="c5", metric="carbon", value=1.0, unit="t",
                      badge=Badge.MODELED, as_of_utc="2026-10-26",
                      calc_id="carbon@0.1.0")
    assert with_calc.renderable()[0]


def test_citation_survives_pdf_typography():
    """PDF extraction mangles dashes, quotes and spacing; comparison doesn't."""
    page = "Section B.3 — The permittee shall  not\tdischarge more than 1.20"
    cit = Citation(doc_sha256="x", page=1, line=1,
                   quote="the permittee shall not discharge").verify(page)
    assert cit.verified


# ---------------------------------------------------------------- INV-8
def test_empty_ghgrp_is_not_zero():
    claim = not_reported("ghg_scope1", "t CO2e", "2026-10-26",
                         NotPublished.BELOW_THRESHOLD,
                         note="facility below the 25,000 t reporting threshold")
    assert claim.value is NotPublished.BELOW_THRESHOLD
    assert claim.value != 0
    d = claim.as_dict()
    assert d["value"] == "BELOW_THRESHOLD"
    assert d["renderable"] is True


# ---------------------------------------------------------------- INV-9
def test_water_is_never_a_scalar():
    band = modelled_band(71.0, CoolingType.EVAPORATIVE)
    assert band.state is WaterState.MODELLED_BAND
    assert band.low_gal_per_day < band.high_gal_per_day
    assert "modelled band" in band.note

    none = modelled_band(71.0, CoolingType.UNKNOWN)
    assert none.state is WaterState.NOT_PUBLISHED
    assert none.low_gal_per_day is None

    with pytest.raises(WaterError):
        from wattdocket.water import WaterClaim
        WaterClaim(state=WaterState.MODELLED_BAND, low_gal_per_day=None,
                   high_gal_per_day=100.0, basis="x", note="y")
    with pytest.raises(WaterError):
        from wattdocket.water import WaterClaim
        WaterClaim(state=WaterState.NOT_PUBLISHED, low_gal_per_day=5.0,
                   high_gal_per_day=10.0, basis="x")


# ---------------------------------------------------------------- INV-10
def test_numbers_in_prose_all_trace_to_slots():
    """The model may only restate numbers it was handed.

    This is the guard the renderer uses: extract every numeral from model
    prose and require each to appear in the computed payload.
    """
    from wattdocket.prose import extract_numerals, prose_is_bound

    payload = {"annual_tonnes_co2e": 154017.0, "p10": 115512.0,
               "p90": 200222.0, "mw": 84.0}
    good = ("This campus emits about 154,017 tonnes a year, on 84 MW of "
            "contracted power.")
    assert prose_is_bound(good, payload)

    bad = "This campus emits about 190,300 tonnes a year."
    assert not prose_is_bound(bad, payload)
    assert "190300" in extract_numerals(bad)


# ---------------------------------------------------------------- estimator contract
def test_band_widens_when_inputs_are_unknown():
    known = estimate(EstimateInput(
        capacity_mw=100, capacity_basis=CapacityBasis.GRID_MW,
        utilization=0.65, grid_rate_lb_per_mwh=593.4))
    unknown = estimate(EstimateInput(
        capacity_mw=100, capacity_basis=CapacityBasis.UNKNOWN,
        grid_rate_lb_per_mwh=593.4))
    assert unknown.band_ratio > known.band_ratio
    assert "capacity_basis_unknown" in unknown.drivers
    assert unknown.badge is EstBadge.ESTIMATED


def test_cars_helper_uses_metric_tonnes():
    assert cars_equivalent(46.0) == pytest.approx(10.0)


# ---------------------------------------------------------------- INV-6 (hash)
def test_factor_set_hash_matches():
    """The default factor set must carry the hash of its own table.

    Without this, a provisional number links to a placeholder and INV-6 is a
    claim rather than an enforcement — which is exactly what a reviewer found
    in the first draft.
    """
    from wattdocket.grid import DEFAULT_FACTOR_SET, factor_set_hash

    assert factor_set_hash(DEFAULT_FACTOR_SET.lb_per_mwh) == DEFAULT_FACTOR_SET.sha256
    assert DEFAULT_FACTOR_SET.sha256 != "0" * 64


# ---------------------------------------------------------------- claim boundary
def test_cards_never_assert_a_facility_is_a_data_center_on_our_authority():
    """NAICS 518210 is a class, not an identification.

    The ECHO extract includes hosting firms and at least one paper mill. A card
    may therefore describe "a load at an address", and may only upgrade the
    facility type when a regulator has said so in its own published registry
    (see test_class_verified_is_only_set_by_a_regulator_or_not_at_all).
    """
    import glob
    import json as _json
    import re
    import pathlib

    files = glob.glob(str(pathlib.Path(__file__).parent.parent.parent
                           / "artifacts" / "cards" / "*.json"))
    if not files:
        import pytest as _pytest
        _pytest.skip("no generated cards present")

    violations = []
    for f in files:
        card = _json.load(open(f))
        fac = card["facility"]
        if fac.get("class_verified"):
            if not fac.get("class_match_method"):
                violations.append((f, "verified without a stated match method"))
            if "DEQ" not in fac.get("class_label", ""):
                violations.append((f, "verified without a regulator citation"))
        else:
            if "NAICS" not in fac.get("class_label", ""):
                violations.append((f, "unverified card does not state its class"))
        if re.search(r"\b(a|this) data center\b",
                     card["verdict"]["sentence"], re.I):
            violations.append((f, "verdict asserts it is a data center"))
    assert not violations, violations[:5]


# ---------------------------------------------------------------- geo binding
def test_point_in_polygon_matches_the_published_service():
    """Eight cities whose subregion was read from EPA's live ArcGIS service.

    The first implementation bounded only the first ring of each polygon, which
    cropped Alaska to one island and silently returned None for every mainland
    point. This test would have caught it before a single card was generated.
    """
    from wattdocket.geo import coverage, subregion_for_point

    assert coverage()["geometry_loaded"] is True
    cases = [
        ("SRVC", 39.0438, -77.4874),   # Ashburn, VA
        ("ERCT", 32.7767, -96.7970),   # Dallas, TX
        ("CAMX", 37.3541, -121.9552),  # Santa Clara, CA
        ("RFCW", 39.9612, -82.9988),   # Columbus, OH
        ("NYUP", 43.1009, -75.2327),   # Utica, NY
        ("NWPP", 45.8399, -119.7006),  # Boardman, OR
        ("ERCT", 29.481991, -98.700221),  # San Antonio, TX
        ("RMPA", 39.7392, -104.9903),  # Denver, CO
    ]
    for expected, lat, lon in cases:
        assert subregion_for_point(lat, lon) == expected, (lat, lon)


def test_points_outside_coverage_return_none():
    from wattdocket.geo import subregion_for_point

    assert subregion_for_point(51.5, -0.12) is None      # London
    assert subregion_for_point(float("nan"), 0.0) is None


def test_every_card_binds_its_grid_by_geometry_not_by_state():
    """A state-level default is a placeholder, and it is wrong where it matters.

    Texas holds three subregions, so a TX default would put a Dallas load on the
    wrong grid. This test holds the line: every card must be resolved by
    point-in-polygon against the bundled eGRID layer.
    """
    import json as _json
    import pathlib as _pathlib

    index_path = _pathlib.Path(__file__).parent.parent.parent / "artifacts" / "cards.json"
    if not index_path.exists():
        import pytest as _pytest
        _pytest.skip("no index present")
    cards = _json.loads(index_path.read_text())["cards"]
    assert len(cards) == 479
    fallbacks = {k: v for k, v in cards.items()
                 if v.get("binding_method") != "point_in_polygon"}
    assert not fallbacks, list(fallbacks)[:5]


def test_resolver_matches_the_epa_verified_bindings():
    """479 frozen bindings, each confirmed against EPA's live FeatureServer.

    scripts/verify_geo_against_epa.py queried the published layer with the same
    points and found 0 disagreements. This test keeps that guarantee testable
    offline: if anyone retunes the geometry, a point that moves subregion fails
    here instead of silently changing a card's carbon number.
    """
    import json as _json
    import pathlib as _pathlib

    from wattdocket.geo import subregion_for_point

    path = (_pathlib.Path(__file__).parent.parent.parent
            / "data" / "egrid_binding_expected.json")
    if not path.exists():
        import pytest as _pytest
        _pytest.skip("no frozen bindings present")
    expected = _json.loads(path.read_text())
    assert len(expected) == 479
    wrong = {k: (v["subregion"], subregion_for_point(v["lat"], v["lon"]))
             for k, v in expected.items()
             if subregion_for_point(v["lat"], v["lon"]) != v["subregion"]}
    assert not wrong, list(wrong.items())[:5]


def test_class_verified_is_only_set_by_a_regulator_or_not_at_all():
    """NEVER assert a facility type on our own authority.

    A card may say "listed under NAICS 518210" (a classification) or may quote a
    state that issued a data-center permit for it. It may not claim, in its own
    voice, that a building is a data center.
    """
    import glob
    import json as _json

    verified, claimed = [], []
    for f in glob.glob(str(__import__("pathlib").Path(__file__).parent.parent.parent
                           / "artifacts" / "cards" / "*.json")):
        fac = _json.load(open(f))["facility"]
        if fac.get("class_verified"):
            verified.append(fac)
            assert fac.get("class_match_method"), fac.get("name")
            assert str(fac.get("state")).upper() == "VA", fac.get("name")
            assert "DEQ" in fac.get("class_label", ""), fac.get("class_label")
        else:
            claimed.append(fac)
            assert "NAICS" in fac.get("class_label", ""), fac.get("class_label")

    # 111 by exact name, 7 more by the DEQ registration number in EPA's source id.
    assert len(verified) == 118, len(verified)
    assert len(verified) + len(claimed) == 479


def test_rows_are_not_buildings():
    """479 ECHO rows, 466 facilities — and the difference is not a rounding slip.

    12 RegistryIDs appear more than once because one site can hold several air
    permits (Amazon/VADATA at 43831 Devin Shafron Dr appears three times under
    three names). Counting cards as facilities overstated the catalogue by 13 and
    was the last unearned number on the site.
    """
    import csv as _csv
    import json as _json
    import pathlib as _pathlib

    root = _pathlib.Path(__file__).parent.parent.parent
    rows = list(_csv.DictReader((root / "data" / "echo_datacenters_naics518210.csv").open()))
    ids = [r["RegistryID"] for r in rows]
    assert len(rows) == 479
    assert len(set(ids)) == 466

    index_path = root / "artifacts" / "cards.json"
    if not index_path.exists():
        import pytest as _pytest
        _pytest.skip("no index present")
    index = _json.loads(index_path.read_text())
    assert index["count"] == len(rows)
    assert index["sites"] == len(set(ids))
    # Every card must name the site it belongs to, or the two counts cannot be
    # reconciled by a reader.
    missing = [k for k, v in index["cards"].items() if not v.get("site_id")]
    assert not missing, missing[:5]


def test_hero_storage_figure_is_energy_not_capacity():
    """156,260.9 was labelled MW — many times ERCOT's entire battery fleet.

    The value is the sum of excluded *energy* across 288 five-minute intervals,
    i.e. MWh for the day. Deriving mean power from it gives ~6.5 GW, which is
    plausible; the label was the bug, and a mislabelled unit turns a sound
    calculation into a laughable one.
    """
    import json as _json
    import pathlib as _pathlib

    card_path = (_pathlib.Path(__file__).parent.parent.parent
                 / "artifacts" / "cards" / "echo_110072155814.json")
    if not card_path.exists():
        import pytest as _pytest
        _pytest.skip("no hero card")
    hourly = _json.loads(card_path.read_text()).get("hourly")
    assert hourly, "hero card lost its hourly series"
    assert "storage_excluded_mw" not in hourly, "the mislabelled field is back"
    mwh = hourly["storage_excluded_mwh"]
    mw = hourly["storage_excluded_mean_mw"]
    assert 0 < mwh < 400_000, mwh
    assert 0 < mw < 20_000, f"mean storage power implausible: {mw} MW"
    # the two must be consistent with 24 hours of the day
    assert abs(mwh / 24 - mw) < 1.0, (mwh, mw)


def test_subregion_rates_are_the_co2e_column_not_co2():
    """Every card says "lb CO2e/MWh"; the rate must be a CO2e rate.

    The first table was a hand-copied literal labelled CO2e whose values were the
    CO2-only column for 22 of 27 subregions (MROW: 920.1 shipped, CO2e is 926.55).
    Small in magnitude, indefensible in kind — and undetectable by any test that
    only checks the number is in a plausible range.

    The rates are now read from the frozen eGRID extract, and this test pins the
    two columns apart so a hand-edit cannot reintroduce the mix.
    """
    import json as _json
    import pathlib as _pathlib

    from wattdocket import cards as cards_mod

    root = _pathlib.Path(__file__).parent.parent.parent
    extract = _json.loads((root / "data" / "egrid_subregion_rates.json").read_text())
    subs = extract["subregions"]

    assert len(cards_mod.SUBREGIONS) == len(subs)
    mismatched = []
    for code, (_name, rate) in cards_mod.SUBREGIONS.items():
        expected = subs[code]["co2e_rate_lb_per_mwh"]
        if abs(rate - expected) > 0.01:
            mismatched.append((code, rate, expected))
    assert not mismatched, mismatched[:5]

    # And prove the test would catch the original bug: for at least one
    # subregion the two columns must differ enough to be distinguishable.
    distinguishable = [c for c, r in subs.items()
                       if abs(r["co2_rate_lb_per_mwh"] - r["co2e_rate_lb_per_mwh"]) > 0.5]
    assert len(distinguishable) >= 20, len(distinguishable)


def test_carbon_receipt_points_at_the_extract_it_quotes():
    """The carbon claim's citation must hash and quote the same document.

    The first version cited the fuel factor set's sha256 for an eGRID quote and
    set verified=True by hand, so the source drawer printed "substring-verified"
    over a file the quote had never been checked against.
    """
    import hashlib as _hashlib
    import pathlib as _pathlib

    from wattdocket import cards as cards_mod
    from wattdocket.grid import DEFAULT_FACTOR_SET

    root = _pathlib.Path(__file__).parent.parent.parent
    text = (root / "data" / "egrid_subregion_rates.json").read_text()
    sha = _hashlib.sha256(text.encode()).hexdigest()
    lines = text.splitlines()

    for code, (_name, rate) in cards_mod.SUBREGIONS.items():
        c = cards_mod.rate_citation(code)
        assert c.doc_sha256 == sha != DEFAULT_FACTOR_SET.sha256
        assert c.verified and c.quote in text
        assert lines[c.line - 1].strip().rstrip(",") == c.quote
        assert c.quote == f'"co2e_rate_lb_per_mwh": {rate}', (code, c.quote)
        # A verification that cannot fail is not one.
        assert not c.verify(text.replace(c.quote, "")).verified


def test_carbon_card_is_not_keyed_to_the_fuel_factor_set():
    """Annual tonnes come from the eGRID rate, not the hourly fuel factors.

    Stamping the factor-set id on the carbon calc meant a gas-factor revision
    would have changed every card's receipt while changing none of its numbers.
    """
    from wattdocket import cards as cards_mod
    from wattdocket.grid import DEFAULT_FACTOR_SET

    row = {"RegistryID": "110000000001", "FacName": "Test", "FacLat": "38.95",
           "FacLong": "-77.45", "AIRState": "VA"}
    card = cards_mod.build_card(row, "2026-10-29T00:00:00Z")
    carbon = next(c for c in card["claims"] if c["id"] == "carbon-t")
    assert DEFAULT_FACTOR_SET.id not in carbon["calc_id"]
    assert "egrid2023rev2" in carbon["calc_id"]
    assert all(c["verified"] for c in carbon["citations"])


def test_unknown_subregion_refuses_rather_than_inventing_a_rate():
    from wattdocket import cards as cards_mod

    orig = cards_mod.resolve_subregion
    cards_mod.resolve_subregion = lambda lat, lon, state: ("NOPE", "manual")
    try:
        with pytest.raises(ValueError, match="no row in the eGRID extract"):
            cards_mod.build_card({"FacLat": "38.95", "FacLong": "-77.45",
                                  "AIRState": "VA"}, "2026-10-29T00:00:00Z")
    finally:
        cards_mod.resolve_subregion = orig
