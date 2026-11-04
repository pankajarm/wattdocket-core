"""The permit envelope: two caps from one permit, and which one binds.

Virginia permits state a run-hours cap and a fuel-throughput cap that have to
hold *simultaneously*. Those two numbers alone determine whether the site can
actually use the hours it is permitted — and the answer is often no.

That is the first real join between the two faces of the product: the public card
says what the state allows, the operator bench says what that means for a year.
No new data, no model, no factor table beyond one rule of thumb that is labelled
as one.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent.parent
FACTS = ROOT / "artifacts" / "va_permit_facts.json"
pytestmark = pytest.mark.skipif(not FACTS.exists(), reason="no VA facts")


def _facts() -> dict:
    return json.loads(FACTS.read_text())


def test_equipment_table_sums_units_and_capacity():
    """Amazon IAD-215: 25 gen-sets, 22 x 2,500 + 2 x 2,000 + 1 x 600 ekW.

    The permit's own table, read positionally. 59.6 MW is the sum, and the test
    pins it so a regex change cannot quietly shift the capacity that every
    envelope figure is built on.
    """
    rec = _facts().get("74252-2")
    assert rec, "permit 74252-2 missing from facts"
    equip = rec.get("equipment") or []
    assert equip, "no equipment parsed"
    e = equip[0]
    assert e["value"] == 25
    assert round(e["mw_total"], 1) == 59.6
    assert 2500.0 in e["ratings_ekw"]
    assert e["citation"]["page"] >= 1


def test_equipment_capacity_is_cited_to_a_page():
    for reg in ("74252-2", "74227-2"):
        rec = _facts().get(reg)
        if not rec or not rec.get("equipment"):
            continue
        cit = rec["equipment"][0]["citation"]
        assert cit and cit["page"] >= 1
        assert cit["quote"]


def test_the_two_caps_give_an_envelope_that_can_be_checked():
    """Arithmetic only: MW x hours, and gallons / that."""
    rec = _facts()["74252-2"]
    equip = rec["equipment"][0]
    hours = next(o["value"] for o in rec["obligations"]
                 if o["metric"] == "generator_hours_limit")
    fuel = next(o["value"] for o in rec["obligations"]
                if o["metric"] == "fuel_throughput_limit")
    mw = equip["mw_total"]
    permitted_mwh = mw * hours
    assert round(permitted_mwh) == 29800
    implied = fuel / permitted_mwh
    assert 20 < implied < 30, implied        # 23 gal/MWh
    # The fuel cap cannot support the full hours cap at a realistic burn rate.
    assert implied < 70
    effective = fuel / (70 * mw)
    assert 150 < effective < 180, effective  # ~164 h/yr


def test_most_permits_with_a_table_also_have_a_hours_cap():
    """If these rarely co-occur the envelope is decoration, not a feature."""
    data = _facts()
    both = [
        reg for reg, rec in data.items()
        if rec.get("equipment")
        and any(o["metric"] == "generator_hours_limit" or o["metric"] == "mcrt_hours_limit"
                for o in rec["obligations"])
    ]
    assert len(both) >= 40, len(both)


def test_no_envelope_is_published_when_its_caps_cannot_reconcile():
    """The bug this guards: an envelope claiming 1 reachable hour per year.

    A diesel genset needs ~70 gal/MWh at full load and always more at part load,
    never less. So a fuel cap implying 31 gal/MWh cannot describe the same fleet
    as the equipment table — most likely the cap covers one engine group or a
    fire pump while the table sums the whole campus. The first build published
    the arithmetic anyway ("hours reachable: 1 h/yr" for a 200 MW site).

    An envelope is either internally consistent or not published at all.
    """
    import glob
    import json
    import pathlib

    root = pathlib.Path(__file__).parent.parent.parent
    published = refused = 0
    for f in glob.glob(str(root / "site" / "api" / "operator" / "*")):
        env = json.loads(pathlib.Path(f).read_text()).get("permit_envelope")
        if not env:
            continue
        implied = env.get("implied_gal_per_mwh")
        if env.get("inconsistent"):
            refused += 1
            # A refused envelope must not carry a reachable-hours claim.
            assert env.get("effective_hours") is None, f
            assert implied is None or not 40 <= implied <= 200, (f, implied)
        else:
            published += 1
            if implied is not None:
                # Two-sided: a cap implying 583 gal/MWh is as impossible as one
                # implying 23, and a one-sided floor published it.
                assert 40 <= implied <= 200, (f, implied)
            if env.get("effective_hours") is not None:
                assert env["effective_hours"] > 0
                assert env["binding"] in ("fuel", "hours", "neither")

    assert published > 0, "no envelope published anywhere"
    assert refused > 0, "the refusal path is never exercised by real data"


def test_a_tied_cap_is_not_reported_as_binding():
    """At exactly the rule-of-thumb burn rate the two caps coincide.

    One payload computed effective = 100.0 h against a 100 h permit and reported
    "fuel binds first" — inventing a constraint where the caps merely agree.
    """
    import glob
    import json
    import pathlib

    root = pathlib.Path(__file__).parent.parent.parent
    for f in glob.glob(str(root / "site" / "api" / "operator" / "*")):
        env = json.loads(pathlib.Path(f).read_text()).get("permit_envelope") or {}
        if env.get("binding") == "neither":
            assert abs(env["effective_hours"] - env["hours_cap"]) < 1.0, f
        if env.get("binding") in ("fuel", "hours") and env.get("effective_hours"):
            if env["binding"] == "fuel":
                assert env["effective_hours"] < env["hours_cap"], f
            else:
                assert env["effective_hours"] > env["hours_cap"], f
