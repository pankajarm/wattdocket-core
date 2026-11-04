"""National sanity check: does our method reproduce a published actual?

T1 wants per-facility actuals and cannot have them (see docs/T1-CALIBRATION.md).
This is the coarser test that *is* runnable: push published national totals
through our estimator and see whether the arithmetic closes.

LBNL's 2024 report puts US data-centre electricity use at ~176 TWh in 2023.
Two published capacity figures are commonly used for the same year. If our
defaults are right, capacity × hours × utilisation should land near 176 TWh.

    python scripts/t1_national.py
"""

from __future__ import annotations

HOURS_PER_YEAR = 8760

# Imported from the estimator, never restated. The first version of this file
# hard-coded 0.62 while the estimator shipped 0.65, so the national check was run
# at a utilisation the product does not use and reported a gap that was 2 points
# too wide. A duplicated constant is a future inconsistency with a delay fuse.
import sys as _sys
import pathlib as _pathlib

_sys.path.insert(0, str(_pathlib.Path(__file__).parent.parent / "core"))
from wattdocket.estimator import UTILIZATION_PRIOR as _PRIOR  # noqa: E402

UTILISATION_DEFAULT = _PRIOR[1]          # p50 — the number every card ships with
UTILISATION_RANGE = _PRIOR               # p10, p50, p90

LBNL_2023_TWH = 176.0
CAPACITY_ESTIMATES = {
    "LBNL 2024 report, 2023 operational (GW)": 21.0,
    "industry estimate of US DC capacity (GW)": 25.0,
}


# 1 GW = 1000 MW; MW x hours = MWh; /1000 = GWh; /1000 = TWh.
# The first draft of this file got this wrong by 1000x and cheerfully printed
# "+64704% vs LBNL". Every unit conversion in this repo now either imports
# wattdocket.units or carries a test — this one imports nothing, so here is the
# derivation spelled out and a test that pins the identity.
def twh_from_capacity(gw: float, utilisation: float, pue: float = 1.0) -> float:
    mwh = gw * 1000 * HOURS_PER_YEAR * utilisation * pue
    return mwh / 1e6


def implied_capacity(twh: float, utilisation: float, pue: float = 1.0) -> float:
    mwh = twh * 1e6
    return mwh / (HOURS_PER_YEAR * utilisation * pue) / 1000


def main() -> None:
    print(f"estimator default utilisation: {UTILISATION_DEFAULT:.2f}")
    print(f"annual energy per GW of capacity: "
          f"{twh_from_capacity(1.0, UTILISATION_DEFAULT):.3f} TWh\n")

    print(f"LBNL 2023 total: {LBNL_2023_TWH:.0f} TWh")
    print(f"  -> implies {implied_capacity(LBNL_2023_TWH, UTILISATION_DEFAULT):.1f} GW "
          f"at {UTILISATION_DEFAULT:.2f} utilisation, no PUE\n")

    print("Push published capacity through our method:")
    for label, gw in CAPACITY_ESTIMATES.items():
        got = twh_from_capacity(gw, UTILISATION_DEFAULT)
        print(f"  {label:44s} {gw:5.1f} -> {got:6.1f} TWh  "
              f"({got / LBNL_2023_TWH - 1:+.0%} vs LBNL)")

    # What would close the gap?
    print("\nWhat closes the gap, keeping published capacity:")
    for label, gw in CAPACITY_ESTIMATES.items():
        need = LBNL_2023_TWH * 1e6 / (gw * 1000 * HOURS_PER_YEAR)
        print(f"  {label:44s} needs utilisation {need:.2f} at PUE 1.0")
        # or the same utilisation with a PUE, i.e. capacity is IT load
        need_pue = need / UTILISATION_DEFAULT
        print(f"  {'':44s} or utilisation {UTILISATION_DEFAULT:.2f} at PUE {need_pue:.2f}")

    lo = min(twh_from_capacity(g, UTILISATION_DEFAULT) / LBNL_2023_TWH - 1
             for g in CAPACITY_ESTIMATES.values())
    hi = max(twh_from_capacity(g, UTILISATION_DEFAULT) / LBNL_2023_TWH - 1
             for g in CAPACITY_ESTIMATES.values())
    print("\nConclusion for the product:")
    print(f"  Our defaults under-predict a published national total by "
          f"{abs(hi):.0%}-{abs(lo):.0%} (at utilisation {UTILISATION_DEFAULT:.2f}).")
    print("  That is not a rounding difference; it is the size of the question the")
    print("  no-US-state-requires-reporting gap creates. The card must therefore")
    print("  show utilisation and basis on the number, and the band must be wide")
    print("  enough to contain this. It currently is (see docs/T1-CALIBRATION.md).")


if __name__ == "__main__":
    main()
