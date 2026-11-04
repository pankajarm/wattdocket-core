"""Precompute the "typical 100 MW facility here" table for the address fallback.

An address that is not in the 479-row ECHO index still sits in an eGRID
subregion, and the subregion decides the emission rate. So the honest answer
for an arbitrary US address is not a facility card: it is what a 100 MW data
center *would* draw and emit on that grid, as a range, badged ESTIMATED.

The browser geocodes and does the point-in-polygon lookup; it does not do the
math. Every number it shows comes from this file, which the same estimator that
builds the cards wrote, with the same rates and the same factor set.

    python scripts/build_resolve_fallback.py \
        --out web/public/data/resolve-fallback.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from wattdocket.cards import STATE_DEFAULT, SUBREGIONS, rate_citation  # noqa: E402
from wattdocket.estimator import (  # noqa: E402
    HOURS_PER_YEAR,
    UTILIZATION_PRIOR,
    CapacityBasis,
    EstimateInput,
    estimate,
)
from wattdocket.grid import DEFAULT_FACTOR_SET  # noqa: E402

REFERENCE_MW = 100.0
CALC_VERSION = "resolve_fallback@0.1.0"


def _citation(sub: str) -> dict:
    c = rate_citation(sub)
    return {
        "publisher": c.publisher,
        "dataset": c.dataset,
        "field": c.field,
        "quote": c.quote,
        "doc_sha256": c.doc_sha256,
        "page": c.page,
        "line": c.line,
        "verified": c.verified,
    }


def build() -> dict:
    subregions = {}
    for sub, (name, rate) in sorted(SUBREGIONS.items()):
        est = estimate(EstimateInput(
            capacity_mw=REFERENCE_MW,
            capacity_basis=CapacityBasis.UNKNOWN,
            grid_rate_lb_per_mwh=rate,
            factor_set_id=DEFAULT_FACTOR_SET.id,
        ))
        lo_mwh = REFERENCE_MW * HOURS_PER_YEAR * UTILIZATION_PRIOR[0]
        hi_mwh = REFERENCE_MW * HOURS_PER_YEAR * UTILIZATION_PRIOR[2]
        subregions[sub] = {
            "name": name,
            "rate_lb_per_mwh": rate,
            "badge": est.badge.value,
            "energy_mwh": {
                "p10": round(lo_mwh, 1),
                "p50": round(est.annual_mwh, 1),
                "p90": round(hi_mwh, 1),
            },
            "carbon_t": {
                "p10": round(est.p10_tonnes, 1),
                "p50": round(est.annual_tonnes_co2e, 1),
                "p90": round(est.p90_tonnes, 1),
            },
            "drivers": est.drivers,
            "chain": [
                f"{REFERENCE_MW:.0f} MW reference capacity (stated, not this site's)",
                f"x 8760 h x {est.assumptions['utilization']:.2f} utilisation "
                f"(LBNL prior, {UTILIZATION_PRIOR[0]:.2f}-{UTILIZATION_PRIOR[2]:.2f})",
                f"= {est.annual_mwh:,.0f} MWh/yr",
                f"x {rate:.1f} lb CO2e/MWh (eGRID2023, {sub})",
                f"/ 2204.6226 lb per metric tonne = {est.annual_tonnes_co2e:,.0f} t CO2e/yr",
            ],
            # The same receipt the cards carry: the extract's own line, its own
            # hash, substring-verified. The first version quoted a sentence
            # built here with an f-string and cited the factor set's hash, so
            # the page showed quotation marks around words no source contains.
            "citation": _citation(sub),
        }

    return {
        "calc_version": CALC_VERSION,
        "reference_mw": REFERENCE_MW,
        "capacity_basis": CapacityBasis.UNKNOWN.value,
        "utilization_prior": list(UTILIZATION_PRIOR),
        "factor_set": {
            "id": DEFAULT_FACTOR_SET.id,
            "sha256": DEFAULT_FACTOR_SET.sha256,
            "url": DEFAULT_FACTOR_SET.url,
        },
        "geometry": "EPA eGRID2023 subregion polygons, simplified to 110 m "
                    "(data/egrid_subregions.geojson), the same file the cards use",
        "state_default": STATE_DEFAULT,
        "subregions": subregions,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "web" / "public" / "data" / "resolve-fallback.json"))
    args = ap.parse_args()
    out = Path(args.out)
    table = build()
    out.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(table['subregions'])} subregions)")


if __name__ == "__main__":
    main()
