"""ERCOT 5-minute fuel mix -> :class:`IntervalReading` rows.

Responsible for: INV-2 (UTC storage), INV-3 (publisher label + hour
convention), INV-5 (storage out of generation *and* out of the intensity
numerator) and INV-6 (derived intensity is PROVISIONAL and names its factor
set). The arithmetic itself is **not** reimplemented here — every interval is
scored by :func:`wattdocket.grid.intensity_from_fuel_mix`, so the storage and
factor-set rules exist in exactly one place.

Publisher quirks closed here:

* Keys are interval-STARTING local labels carrying a fixed offset
  (``2026-10-27 00:00:00-0500``); they are parsed with
  :func:`wattdocket.timeutil.parse_offset_iso` and stored UTC.
* Each payload carries ``Power Storage`` *plus* ``Power Storage Charging`` and
  ``Power Storage Discharging``. The aggregate is the sum of the components, so
  keeping all three would triple-count the transfer; the components win and the
  aggregate is dropped.
* A missing fuel is a gap, not a zero: we require the six generating fuels to
  be present in every interval and raise naming the label when one is absent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from ..grid import DEFAULT_FACTOR_SET, IntensityState, intensity_from_fuel_mix
from ..timeutil import parse_offset_iso
from .net import Fetcher, IngestionError

ERCOT_URL = "https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json"
BA_CODE = "ERCO"
INTERVAL_MINUTES = 5
HOUR_CONVENTION = "START"          # ERCOT labels the start of the interval
INTENSITY_STATE = IntensityState.PROVISIONAL

# Fuels ERCOT must publish in every interval for a reading to be meaningful.
REQUIRED_FUELS = ("Coal and Lignite", "Natural Gas", "Nuclear", "Wind", "Solar",
                  "Hydro", "Other")
STORAGE_COMPONENTS = ("Power Storage Charging", "Power Storage Discharging")
STORAGE_AGGREGATE = "Power Storage"


class ErcotError(IngestionError):
    pass


def available_days(payload: dict) -> list[str]:
    """Days present in the dashboard payload, sorted (ISO date strings)."""
    data = _data_block(payload)
    return sorted(data)


def _data_block(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ErcotError("ERCOT payload is not a JSON object", ERCOT_URL)
    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        raise ErcotError(
            f"ERCOT payload has no populated 'data' block; keys={list(payload)}",
            ERCOT_URL)
    return data


def _fuels_for_interval(fuels: dict, label: str) -> dict[str, float]:
    """Raw ERCOT fuel dict -> {fuel: MW}, storage split out and de-duplicated."""
    if not isinstance(fuels, dict):
        raise ErcotError(f"interval {label} has no fuel breakdown", ERCOT_URL)
    out: dict[str, float] = {}
    for name, body in fuels.items():
        if not isinstance(body, dict) or "gen" not in body:
            raise ErcotError(f"interval {label} fuel {name!r} has no 'gen' value",
                             ERCOT_URL)
        out[name] = float(body["gen"] or 0.0)

    has_components = any(name in out for name in STORAGE_COMPONENTS)
    if has_components and STORAGE_AGGREGATE in out:
        # The aggregate equals the sum of the components; keep the components.
        out.pop(STORAGE_AGGREGATE)
    missing = [fuel for fuel in REQUIRED_FUELS if fuel not in out]
    if missing:
        raise ErcotError(
            f"interval {label} is missing required fuels {missing}; refusing a "
            f"partial mix", ERCOT_URL)
    return out


def parse_day(payload: dict, day: str | None = None,
              retrieved_at: str | None = None) -> list[dict]:
    """Parse one day of the ERCOT dashboard into interval rows.

    ``day`` defaults to the latest day present. Rows are returned sorted by
    ``interval_start_utc`` so the ledger insert order is deterministic.
    """
    data = _data_block(payload)
    if day is None:
        day = sorted(data)[-1]
    if day not in data:
        raise ErcotError(f"day {day!r} is not in the ERCOT payload "
                         f"(available: {sorted(data)})", ERCOT_URL)
    intervals = data[day]
    if not isinstance(intervals, dict) or not intervals:
        raise ErcotError(f"day {day!r} has no intervals", ERCOT_URL)

    stamp = retrieved_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: list[dict] = []
    for label in sorted(intervals):
        fuels = _fuels_for_interval(intervals[label], label)
        start_utc = parse_offset_iso(label)
        intensity = intensity_from_fuel_mix(fuels, factor_set=DEFAULT_FACTOR_SET,
                                            state=INTENSITY_STATE)
        rows.append({
            "ba_code": BA_CODE,
            "interval_start_utc": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval_minutes": INTERVAL_MINUTES,
            "label": label,
            "hour_convention": HOUR_CONVENTION,
            "gen_mwh_by_fuel": dict(sorted(intensity.fuel_mix.items())),
            "total_gen_mwh": intensity.total_gen_mwh,
            "intensity_lb_co2e_per_mwh": intensity.value_lb_per_mwh,
            "intensity_state": intensity.state.value,
            "factor_set_id": intensity.factor_set_id,
            "storage_excluded_mwh": intensity.storage_excluded_mwh,
            "source_id": "ercot-fuel-mix-dashboard",
            "source_url": ERCOT_URL,
            "retrieved_at_utc": stamp,
        })

    rows.sort(key=lambda r: r["interval_start_utc"])
    _check_no_gaps(rows)
    return rows


def _check_no_gaps(rows: list[dict]) -> None:
    """Consecutive intervals must be one interval apart; a gap is reported."""
    if len(rows) < 2:
        return
    previous = datetime.strptime(rows[0]["interval_start_utc"], "%Y-%m-%dT%H:%M:%SZ")
    for row in rows[1:]:
        current = datetime.strptime(row["interval_start_utc"], "%Y-%m-%dT%H:%M:%SZ")
        delta_minutes = (current - previous).total_seconds() / 60.0
        if delta_minutes != INTERVAL_MINUTES:
            raise ErcotError(
                f"ERCOT gap: {previous.isoformat()} -> {current.isoformat()} is "
                f"{delta_minutes:g} min, expected {INTERVAL_MINUTES}; a gap must be "
                f"labelled, not interpolated", ERCOT_URL)
        previous = current


def load_intervals(fetcher: Fetcher, day: str | None = None,
                   retrieved_at: str | None = None,
                   payload: dict | None = None) -> list[dict]:
    """Fetch (unless a payload is supplied) and parse one day."""
    body: dict = payload if payload is not None else fetcher.get_json(ERCOT_URL)
    return parse_day(body, day=day, retrieved_at=retrieved_at)


def day_summary(rows: Iterable[dict]) -> dict:
    """The numbers the intensity chart quotes, from stored rows only."""
    materialised = list(rows)
    values = [r["intensity_lb_co2e_per_mwh"] for r in materialised]
    if not values:
        raise ErcotError("no intervals to summarise", ERCOT_URL)
    return {
        "intervals": len(values),
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "ratio": max(values) / min(values) if min(values) else None,
        "state": materialised[0]["intensity_state"],
    }
