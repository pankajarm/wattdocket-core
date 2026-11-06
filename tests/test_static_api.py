"""Portable static-payload tests.

CloudFront routing/configuration tests stay with the private deployment source;
this public suite exercises the shared deterministic payload builder only.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_static_api as bsa  # noqa: E402


def test_method_carries_the_pinned_factor_set():
    m = bsa.method_payload("2026-10-29T00:00:00Z")
    fs = m["factor_sets"][0]
    assert fs["sha256"] == bsa.DEFAULT_FACTOR_SET.sha256
    assert m["receipts"] and m["calc_version"] and m["as_of"]
    assert len(m["invariants"]) == 10


def test_grid_day_is_the_captured_day_only():
    fx = ROOT / "core" / "tests" / "fixtures" / "ercot_fuel_mix.json"
    days = bsa.grid_payloads(fx, fx.parent / "manifest.json", "t")
    assert set(days) == {"2026-10-26", "latest"}
    d = days["latest"]
    assert len(d["means_lb_per_mwh"]) == 24 and d["intervals"] == 288
    assert d["intensity_state"] == "PROVISIONAL"
    assert d["receipts"][0]["sha256_source"]


def test_estimator_grid_uses_the_card_rates():
    files, index = bsa.estimator_grid("t")
    assert len(files) == len(bsa.SUBREGIONS) * len(bsa.ESTIMATOR_CAPS_MW)
    erct = files["erct/100"]
    assert erct["grid_rate_lb_per_mwh"] == bsa.SUBREGIONS["ERCT"][1]
    e = erct["estimate"]
    assert e["p10_tonnes"] < e["annual_tonnes_co2e"] < e["p90_tonnes"]
    assert index["cap_mw"] == list(bsa.ESTIMATOR_CAPS_MW)
