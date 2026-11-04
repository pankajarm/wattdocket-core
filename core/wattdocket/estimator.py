"""The estimator — INV-4 (capacity basis), and the band contract.

The estimator's job is not to produce a number. It is to produce a *range*
with the reasons for its width attached, because that range and those
reasons are the product. A point estimate would be a lie dressed as
precision, and it is exactly what every incumbent publishes.

Refusals encoded here:

* Capacity basis must be stated. Applying PUE to a figure that is already
  whole-facility power double-counts cooling by 25%.
* An unknown basis does not silently become GRID_MW; it widens the band and
  says so.
* Utilization is not a guess with a default: it is either supplied or it is
  the published national prior with its range, and that choice is visible in
  the output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .units import UnitError, mwh_at_rate_to_tonnes

HOURS_PER_YEAR = 8760

# LBNL/DOE 2024 US Data Center Energy Usage Report priors.
UTILIZATION_PRIOR = (0.45, 0.65, 0.80)     # p10, p50, p90
PUE_PRIOR = (1.12, 1.25, 1.45)


class CapacityBasis(str, Enum):
    IT_MW = "IT_MW"        # critical IT load; cooling overhead NOT included
    GRID_MW = "GRID_MW"    # interconnection / whole-facility power
    UNKNOWN = "UNKNOWN"


class Badge(str, Enum):
    MEASURED = "MEASURED"
    REPORTED = "REPORTED"
    MODELED = "MODELED"
    ESTIMATED = "ESTIMATED"


@dataclass(frozen=True)
class EstimateInput:
    capacity_mw: float
    capacity_basis: CapacityBasis
    utilization: float | None = None          # None -> national prior
    pue: float | None = None                  # only meaningful for IT_MW
    grid_rate_lb_per_mwh: float = 0.0
    hours: int = HOURS_PER_YEAR
    factor_set_id: str | None = None

    def __post_init__(self) -> None:
        if self.capacity_mw < 0:
            raise UnitError(f"capacity cannot be negative: {self.capacity_mw}")
        if self.utilization is not None and not 0 < self.utilization <= 1:
            raise UnitError(f"utilization must be in (0,1], got {self.utilization}")
        if self.pue is not None and self.pue < 1.0:
            raise UnitError(
                f"PUE below 1.0 is physically implausible, got {self.pue}"
            )


@dataclass(frozen=True)
class Estimate:
    annual_mwh: float
    annual_tonnes_co2e: float
    p10_tonnes: float
    p90_tonnes: float
    badge: Badge
    drivers: list[str]                 # ordered, widest contributor first
    assumptions: dict
    capacity_basis: CapacityBasis

    @property
    def band_ratio(self) -> float:
        return self.p90_tonnes / self.p10_tonnes if self.p10_tonnes else float("inf")

    def as_dict(self) -> dict:
        return {
            "annual_mwh": round(self.annual_mwh, 1),
            "annual_tonnes_co2e": round(self.annual_tonnes_co2e, 1),
            "p10_tonnes": round(self.p10_tonnes, 1),
            "p90_tonnes": round(self.p90_tonnes, 1),
            "band_ratio": round(self.band_ratio, 3),
            "badge": self.badge.value,
            "capacity_basis": self.capacity_basis.value,
            "drivers": self.drivers,
            "assumptions": self.assumptions,
        }


def _pue_for(inp: EstimateInput) -> tuple[float, str]:
    """PUE applies ONLY to IT-basis capacity. This is the 25% bug."""
    if inp.capacity_basis is CapacityBasis.IT_MW:
        if inp.pue is None:
            return PUE_PRIOR[1], "pue_from_prior"
        return inp.pue, "pue_supplied"
    if inp.capacity_basis is CapacityBasis.GRID_MW:
        if inp.pue is not None and inp.pue != 1.0:
            raise UnitError(
                "PUE must not be applied to GRID_MW capacity: the reported "
                "figure already includes cooling (INV-4)"
            )
        return 1.0, "pue_not_applicable"
    # UNKNOWN: do not apply, but record that the basis itself is a driver
    return 1.0, "pue_unknown_basis"


def estimate(inp: EstimateInput) -> Estimate:
    if inp.grid_rate_lb_per_mwh <= 0:
        raise UnitError("a grid emission rate is required to estimate carbon")

    pue, pue_note = _pue_for(inp)

    if inp.utilization is None:
        util = UTILIZATION_PRIOR[1]
        util_lo, util_hi = UTILIZATION_PRIOR[0], UTILIZATION_PRIOR[2]
        util_note = "utilization_from_prior"
    else:
        util = inp.utilization
        util_lo = util_hi = util
        util_note = "utilization_supplied"

    annual_mwh = inp.capacity_mw * inp.hours * util * pue
    annual_tonnes = mwh_at_rate_to_tonnes(annual_mwh, inp.grid_rate_lb_per_mwh)

    # Band: combine the uncertainty actually present, do not add noise.
    low_mwh = inp.capacity_mw * inp.hours * util_lo * pue
    high_mwh = inp.capacity_mw * inp.hours * util_hi * pue
    if inp.capacity_basis is CapacityBasis.IT_MW and inp.pue is None:
        high_mwh *= PUE_PRIOR[2]
        low_mwh *= PUE_PRIOR[0]

    p10 = mwh_at_rate_to_tonnes(low_mwh, inp.grid_rate_lb_per_mwh)
    p90 = mwh_at_rate_to_tonnes(high_mwh, inp.grid_rate_lb_per_mwh)

    drivers: list[str] = []
    if inp.capacity_basis is CapacityBasis.UNKNOWN:
        drivers.append("capacity_basis_unknown")
    if inp.utilization is None:
        drivers.append("utilization_prior")
    if inp.capacity_basis is CapacityBasis.IT_MW and inp.pue is None:
        drivers.append("pue_prior")
    if not drivers:
        drivers.append("capacity_and_utilization_supplied")

    # Badge: what do we actually know about this facility's power draw?
    if inp.utilization is not None and inp.capacity_basis is CapacityBasis.GRID_MW:
        badge = Badge.REPORTED
    elif inp.capacity_basis is CapacityBasis.UNKNOWN:
        badge = Badge.ESTIMATED
    else:
        badge = Badge.MODELED

    assumptions = {
        "capacity_mw": inp.capacity_mw,
        "capacity_basis": inp.capacity_basis.value,
        "utilization": round(util, 4),
        "utilization_source": util_note,
        "pue": round(pue, 4),
        "pue_source": pue_note,
        "hours": inp.hours,
        "grid_rate_lb_per_mwh": inp.grid_rate_lb_per_mwh,
        "factor_set_id": inp.factor_set_id,
        "mass_unit": "metric_tonne",
    }

    return Estimate(
        annual_mwh=annual_mwh,
        annual_tonnes_co2e=annual_tonnes,
        p10_tonnes=p10,
        p90_tonnes=p90,
        badge=badge,
        drivers=drivers,
        assumptions=assumptions,
        capacity_basis=inp.capacity_basis,
    )


def cars_equivalent(tonnes: float, tonnes_per_car: float = 4.6) -> float:
    """EPA's 4.6 t CO2 per car per year, kept as an explicit constant."""
    if tonnes_per_car <= 0:
        raise UnitError("cars_per_year constant must be positive")
    return tonnes / tonnes_per_car
