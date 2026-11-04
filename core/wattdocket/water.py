"""Water — INV-9: there is no scalar water number, ever.

No US state effectively requires facility-level *operational* water
reporting from privately owned data centers. What exists is a handful of
voluntary disclosures, some open-records scraps, and modelled screening.
Any product that prints "this facility uses X gallons/day" is inventing a
number, and it will be the first number a hostile reader attacks.

So water is a three-state value: DISCLOSED (with a source), MODELLED_BAND
(with the cooling assumption stated), or NOT_PUBLISHED (with the reason).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .claims import Citation


class WaterState(str, Enum):
    DISCLOSED = "DISCLOSED"
    MODELLED_BAND = "MODELLED_BAND"
    NOT_PUBLISHED = "NOT_PUBLISHED"


class CoolingType(str, Enum):
    EVAPORATIVE = "EVAPORATIVE"
    ADIABATIC = "ADIABATIC"
    DRY = "DRY"
    UNKNOWN = "UNKNOWN"


# Litres of water per kWh of IT energy, by cooling type. Ranges from the
# published literature; deliberately wide, because they are wide in reality.
WUE_L_PER_KWH = {
    CoolingType.EVAPORATIVE: (1.4, 2.2),
    CoolingType.ADIABATIC: (0.15, 0.45),
    CoolingType.DRY: (0.0, 0.05),
}

PUE_ASSUMED = 1.25
KWH_PER_MWH = 1000.0
LITRES_PER_GALLON = 3.785411784


class WaterError(ValueError):
    pass


@dataclass(frozen=True)
class WaterClaim:
    state: WaterState
    low_gal_per_day: float | None
    high_gal_per_day: float | None
    basis: str
    citations: tuple[Citation, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if self.state is WaterState.NOT_PUBLISHED:
            if self.low_gal_per_day is not None or self.high_gal_per_day is not None:
                raise WaterError(
                    "NOT_PUBLISHED must not carry a number (INV-9)"
                )
        if self.state is WaterState.MODELLED_BAND:
            if self.low_gal_per_day is None or self.high_gal_per_day is None:
                raise WaterError("a modelled band needs both bounds")
            if not self.note:
                raise WaterError(
                    "a modelled band must state the cooling assumption it rests on"
                )
        if self.state is WaterState.DISCLOSED:
            if not self.citations or not any(c.verified for c in self.citations):
                raise WaterError(
                    "a disclosed water figure needs a verified citation"
                )

    def as_dict(self) -> dict:
        return {
            "state": self.state.value,
            "low_gal_per_day": self.low_gal_per_day,
            "high_gal_per_day": self.high_gal_per_day,
            "basis": self.basis,
            "note": self.note,
        }


def modelled_band(it_mw: float, cooling: CoolingType) -> WaterClaim:
    """Water as a band, from capacity x cooling type x a stated PUE."""
    if cooling is CoolingType.UNKNOWN:
        return not_published(
            reason="cooling type is not stated in any public document"
        )
    if it_mw <= 0:
        raise WaterError(f"IT capacity must be positive, got {it_mw}")
    lo_l_per_kwh, hi_l_per_kwh = WUE_L_PER_KWH[cooling]

    mwh_per_day = it_mw * 24 * PUE_ASSUMED
    kwh_per_day = mwh_per_day * KWH_PER_MWH
    lo_l = kwh_per_day * lo_l_per_kwh
    hi_l = kwh_per_day * hi_l_per_kwh
    return WaterClaim(
        state=WaterState.MODELLED_BAND,
        low_gal_per_day=round(lo_l / LITRES_PER_GALLON, 0),
        high_gal_per_day=round(hi_l / LITRES_PER_GALLON, 0),
        basis=f"{cooling.value.lower()} cooling, PUE {PUE_ASSUMED}",
        note=("we found no facility-level operational water figure for this site "
              "in the sources we use. Reporting is arriving unevenly: New Jersey "
              "P.L. 2026 c.75 requires semiannual facility-level water and energy "
              "reports, first due Nov 2026; Texas Water Code §16.012 makes the "
              "state's data-centre water survey mandatory but most facilities do "
              "not answer. Neither has produced a per-site figure we can cite here. "
              "this is a modelled band, not a measurement"),
    )


def not_published(reason: str) -> WaterClaim:
    return WaterClaim(
        state=WaterState.NOT_PUBLISHED,
        low_gal_per_day=None,
        high_gal_per_day=None,
        basis="none",
        note=reason or "not published",
    )
