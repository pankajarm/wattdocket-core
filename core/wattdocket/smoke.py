"""End-to-end smoke on real public data, through the real core.

Fetches ERCOT's live fuel mix, computes the hourly intensity profile with the
invariants enforced, then estimates a 100 MW campus in each of five eGRID
subregions and prints what the product would show.

    python -m wattdocket.smoke --ercot /path/to/fuel-mix.json

Nothing here calls a model. If this runs, the number path works.
"""

from __future__ import annotations

import json
import urllib.request
from argparse import ArgumentParser

from .estimator import CapacityBasis, EstimateInput, cars_equivalent, estimate
from .grid import daily_profile, intensity_from_fuel_mix
from .timeutil import parse_offset_iso

ERCOT_URL = "https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json"

# eGRID 2023 subregion CO2e rates, lb/MWh (extracted from the official workbook).
SUBREGION_RATES = {
    "NYUP": ("NPCC Upstate NY", 242.8),
    "CAMX": ("WECC California", 430.0),
    "SRVC": ("SERC Virginia/Carolina", 596.3),
    "ERCT": ("ERCOT All", 736.6),
    "RFCW": ("RFC West", 916.1),
}


def load_ercot(path: str | None) -> dict:
    if path:
        with open(path) as fh:
            return json.load(fh)
    req = urllib.request.Request(ERCOT_URL, headers={"User-Agent": "wattdocket/0.1"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def profile_for_day(payload: dict, day: str) -> list:
    intervals = []
    for label, fuels in sorted(payload["data"][day].items()):
        gen = {k: v.get("gen", 0.0) for k, v in fuels.items()}
        try:
            iv = intensity_from_fuel_mix(gen)
        except Exception:
            continue
        start = parse_offset_iso(label)
        intervals.append((start, iv))
    return intervals


def main() -> None:
    ap = ArgumentParser()
    ap.add_argument("--ercot", default=None, help="local fuel-mix.json")
    args = ap.parse_args()

    payload = load_ercot(args.ercot)
    day = sorted(payload["data"])[0]
    intervals = profile_for_day(payload, day)
    ordered = [iv for _, iv in sorted(intervals, key=lambda t: t[0])]

    prof = daily_profile(ordered)
    print(f"ERCOT {day}: {prof['intervals']} intervals, "
          f"{prof['state']} ({prof['factor_set_id']})")
    print(f"  mean {prof['mean']:.0f} · min {prof['min']:.0f} · "
          f"max {prof['max']:.0f} · swing {prof['ratio']:.2f}x")

    lo = min(zip(ordered, sorted(intervals, key=lambda t: t[0])), key=lambda z: z[0].value_lb_per_mwh)
    print(f"  cleanest {lo[0].value_lb_per_mwh:.0f} at "
          f"{lo[1][0].strftime('%H:%M')} UTC · storage excluded "
          f"{lo[0].storage_excluded_mwh:.0f} MW")

    print("\n100 MW campus, 65% utilisation, GRID basis (PUE correctly not applied):")
    print(f"{'region':26s} {'lb/MWh':>8s} {'t/yr':>9s} {'cars':>7s} {'badge':>9s}")
    for code, (name, rate) in sorted(SUBREGION_RATES.items(), key=lambda kv: kv[1][1]):
        est = estimate(EstimateInput(
            capacity_mw=100, capacity_basis=CapacityBasis.GRID_MW,
            utilization=0.65, grid_rate_lb_per_mwh=rate))
        print(f"{name:26s} {rate:8.1f} {est.annual_tonnes_co2e:9,.0f} "
              f"{cars_equivalent(est.annual_tonnes_co2e):7,.0f} {est.badge.value:>9s}")

    print("\nUnknown basis (what an address with a bare parcel number looks like):")
    est = estimate(EstimateInput(capacity_mw=100,
                                 capacity_basis=CapacityBasis.UNKNOWN,
                                 grid_rate_lb_per_mwh=596.3))
    d = est.as_dict()
    print(f"  p50 {d['annual_tonnes_co2e']:,.0f} t · "
          f"p10–p90 {d['p10_tonnes']:,.0f}–{d['p90_tonnes']:,.0f} · "
          f"band {d['band_ratio']}x · drivers {d['drivers']}")


if __name__ == "__main__":
    main()
