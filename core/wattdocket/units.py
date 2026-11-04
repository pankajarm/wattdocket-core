"""Units — INV-1: mass is metric tonnes, always, from pounds.

The bug this exists to prevent: eGRID publishes emission *rates* in
lb CO2/MWh. Dividing by 2000 gives US short tons, which is ~9.2% higher
than a metric tonne. Review caught the resulting error in an early
draft. There is exactly one conversion factor in
this codebase and it lives here.
"""

LB_PER_METRIC_TONNE = 2204.6226218487757
LB_PER_SHORT_TON = 2000.0


class UnitError(ValueError):
    """Raised when a caller tries to produce a non-metric mass."""


def lb_to_metric_tonnes(lb: float) -> float:
    """The only legal lb -> mass conversion in the codebase."""
    return lb / LB_PER_METRIC_TONNE


def mwh_at_rate_to_tonnes(mwh: float, lb_per_mwh: float) -> float:
    """Energy x emission rate -> metric tonnes CO2e."""
    if mwh < 0:
        raise UnitError(f"energy cannot be negative: {mwh}")
    if lb_per_mwh < 0:
        raise UnitError(f"emission rate cannot be negative: {lb_per_mwh}")
    return lb_to_metric_tonnes(mwh * lb_per_mwh)


def short_tons(lb: float) -> float:
    """Deliberately named so any use is obvious in review, and refused."""
    raise UnitError(
        "short tons are not used anywhere in WattDocket; use lb_to_metric_tonnes()"
    )


def co2e_rate_from_annual(lb_co2e: float, gen_mwh: float) -> float:
    """Published annual mass and generation -> lb CO2e per MWh.

    eGRID's SRCO2EQA / SRCO2AN columns are *short tons per year*; the rate
    columns (SRCO2RTA) are already lb/MWh. This helper converts the former
    into the latter so both paths agree.
    """
    if gen_mwh <= 0:
        raise UnitError(f"generation must be positive, got {gen_mwh}")
    return (lb_co2e * LB_PER_SHORT_TON) / gen_mwh
