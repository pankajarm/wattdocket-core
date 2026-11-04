"""Grid intensity — INV-5 (storage excluded) and INV-6 (provisional vs settled).

Two failure modes are encoded here as refusals rather than conventions:

* Battery storage. ERCOT reports charging as *negative* MW. Summing it into
  the generation denominator shrinks total generation and inflates the
  apparent carbon intensity of every other fuel in that interval — a 5-6%
  error at midday, which is exactly when the product wants to say "the grid
  is clean right now". Charging and discharging are excluded from both the
  numerator and the denominator, and the transfer is stated in the UI.

* Provisional vs settled. Intensity derived from fuel mix x a published
  factor table is *provisional*: three plausible factor sets move the level
  by ~20% while the ratio between the cleanest and dirtiest hour stays
  stable at ~1.75x. A provisional series must carry its factor set so the
  reader can see the assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .units import UnitError

# Standard US average emission factors, lb CO2 per MWh generated.
# These are approximations, published here as a versioned factor set so the
# number is never floating free of its assumption.
DEFAULT_FACTORS_LB_PER_MWH = {
    "coal": 2249.0,
    "gas": 1135.0,
    "oil": 1672.0,
    "nuclear": 0.0,
    "wind": 0.0,
    "solar": 0.0,
    "hydro": 0.0,
    "other": 0.0,
}

STORAGE_KEYS = frozenset({
    "power_storage", "power_storage_charging", "power_storage_discharging",
    "storage", "battery", "battery_charging", "battery_discharging",
})

# Publishers name the same fuel differently. ERCOT says "Coal and Lignite"
# and "Natural Gas"; eGRID says "Coal" and "Natural Gas". Map to one key so a
# factor set is written once. An unmapped fuel still refuses (INV-6) — this
# table is for known synonyms, not for guessing.
FUEL_ALIASES = {
    "coal_and_lignite": "coal",
    "coal": "coal",
    "lignite": "coal",
    "natural_gas": "gas",
    "gas": "gas",
    "naturalgas": "gas",
    "petroleum": "oil",
    "oil": "oil",
    "nuclear": "nuclear",
    "wind": "wind",
    "solar": "solar",
    "hydro": "hydro",
    "hydroelectric": "hydro",
    "other": "other",
}


class IntensityState(str, Enum):
    PROVISIONAL = "PROVISIONAL"   # fuel mix x our published factor table
    SETTLED = "SETTLED"           # CEMS / Open Grid Emissions, measured


class FactorSetMissing(RuntimeError):
    pass


@dataclass(frozen=True)
class FactorSet:
    id: str
    published_by: str
    effective_date: str
    lb_per_mwh: dict = field(default_factory=lambda: dict(DEFAULT_FACTORS_LB_PER_MWH))
    url: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.published_by or not self.effective_date:
            raise ValueError("a factor set must be identifiable: id, publisher, date")
        for fuel, v in self.lb_per_mwh.items():
            if v < 0:
                raise UnitError(f"negative emission factor for {fuel}")


DEFAULT_FACTOR_SET = FactorSet(
    id="us-avg-2023.1",
    published_by="WattDocket (compiled from EPA/EIA national averages)",
    effective_date="2023-12-31",
    url="https://www.epa.gov/egrid",
    # sha256 over the canonical JSON of lb_per_mwh (sorted keys, no spaces).
    # Recomputed by core/tests/test_invariants.py::test_factor_set_hash_matches,
    # so a silent table edit cannot keep a stale hash.
    sha256="0d6119a2e5c132fcb6999d9eb6935b1404db4aa7cb9d6da9e33050aa42577fc7",
)


def factor_set_hash(factors: dict) -> str:
    """The canonical hash of a factor table — the link INV-6 requires."""
    import hashlib
    import json
    canon = json.dumps({k: float(v) for k, v in factors.items()},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Intensity:
    value_lb_per_mwh: float
    total_gen_mwh: float
    state: IntensityState
    factor_set_id: str | None
    storage_excluded_mwh: float
    fuel_mix: dict

    def as_dict(self) -> dict:
        return {
            "value_lb_per_mwh": round(self.value_lb_per_mwh, 3),
            "total_gen_mwh": round(self.total_gen_mwh, 3),
            "state": self.state.value,
            "factor_set_id": self.factor_set_id,
            "storage_excluded_mwh": round(self.storage_excluded_mwh, 3),
            "fuel_mix": {k: round(v, 3) for k, v in self.fuel_mix.items()},
        }


def _normalise(fuel: str) -> str:
    return fuel.strip().lower().replace(" ", "_").replace("&", "and")


def intensity_from_fuel_mix(
    gen_by_fuel_mw: dict[str, float],
    factor_set: FactorSet | None = DEFAULT_FACTOR_SET,
    state: IntensityState = IntensityState.PROVISIONAL,
) -> Intensity:
    """Fuel mix (MW over one interval) -> lb CO2/MWh.

    Storage is removed from both sides. A negative MW value for a non-storage
    fuel is a data error, not something to silently absorb.
    """
    if state is IntensityState.PROVISIONAL and factor_set is None:
        raise FactorSetMissing(
            "a PROVISIONAL intensity must carry its factor set (INV-6)"
        )
    factors = factor_set.lb_per_mwh if factor_set else {}

    included: dict[str, float] = {}
    storage = 0.0

    # ERCOT publishes `Power Storage` (a net figure) *and* `Power Storage
    # Charging` / `Power Storage Discharging` (its components). Summing all three
    # counts the same energy twice — measured on 2026-10-26 00:00 the net figure
    # and the charging component are both -1339.70. When components exist, they
    # are authoritative and the net aggregate is ignored.
    keys_lower = {k.lower(): k for k in gen_by_fuel_mw}
    has_components = any(
        "charg" in k for k in keys_lower
    )
    for raw_fuel, mw in gen_by_fuel_mw.items():
        fuel = _normalise(raw_fuel)
        if fuel in STORAGE_KEYS:
            is_net_aggregate = (
                has_components
                and "charg" not in fuel
                and "discharg" not in fuel
            )
            if not is_net_aggregate:
                storage += float(mw)
            continue
        fuel = FUEL_ALIASES.get(fuel, fuel)
        value = float(mw)
        if value < 0:
            raise UnitError(
                f"negative generation for non-storage fuel {raw_fuel!r}: {value}"
            )
        if value > 0:
            included[fuel] = included.get(fuel, 0.0) + value

    total = sum(included.values())
    if total <= 0:
        raise UnitError("no generation to compute an intensity from")

    co2_lb = 0.0
    for fuel, mw in included.items():
        factor = factors.get(fuel)
        if factor is None:
            # Unknown fuel: do not invent a factor, and do not silently drop
            # the energy either. Refuse, so the factor set gets extended.
            raise FactorSetMissing(
                f"fuel {fuel!r} has no emission factor in set "
                f"{factor_set.id if factor_set else None!r}"
            )
        co2_lb += mw * factor

    return Intensity(
        value_lb_per_mwh=co2_lb / total,
        total_gen_mwh=total,
        state=state,
        factor_set_id=factor_set.id if factor_set else None,
        storage_excluded_mwh=storage,
        fuel_mix=included,
    )


def daily_profile(intervals: list[Intensity]) -> dict:
    """Summarise a day: mean, extremes, ratio — the numbers the UI quotes."""
    if not intervals:
        raise UnitError("no intervals")
    values = [i.value_lb_per_mwh for i in intervals]
    lo, hi = min(values), max(values)
    return {
        "intervals": len(values),
        "mean": sum(values) / len(values),
        "min": lo,
        "max": hi,
        "ratio": (hi / lo) if lo else None,
        "state": intervals[0].state.value,
        "factor_set_id": intervals[0].factor_set_id,
    }
