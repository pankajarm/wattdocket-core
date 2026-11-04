"""Time handling — INV-2 (UTC storage, DST-safe) and INV-3 (hour labels).

Two real traps this module exists to close:

1. US daylight time ends on 1 November 2026, *inside* the build week. That
   local day has 25 hours. Any pipeline that joins on a local "hour" string
   silently double-counts or drops an hour on that day.
2. Publishers disagree on whether a timestamp is the start or the end of the
   interval. EIA-930 stamps are hour-ENDING; ERCOT fuel-mix keys are
   interval-STARTING with a local UTC offset. Mixing them shifts every
   number by one hour — invisible in a daily total, wrong in every hourly
   chart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class TimeError(ValueError):
    pass


@dataclass(frozen=True)
class Interval:
    """One grid interval, with both its published label and a UTC key."""

    start_utc: datetime
    minutes: int
    label: str               # exactly as the publisher wrote it
    hour_convention: str     # "START" | "END"

    def __post_init__(self) -> None:
        if self.start_utc.tzinfo is None:
            raise TimeError("start_utc must be timezone-aware (UTC)")
        if self.start_utc.utcoffset() != timedelta(0):
            raise TimeError("start_utc must be UTC, not merely tz-aware")
        if self.minutes <= 0:
            raise TimeError(f"interval length must be positive, got {self.minutes}")
        if self.hour_convention not in ("START", "END"):
            raise TimeError(f"unknown hour convention {self.hour_convention!r}")

    @property
    def end_utc(self) -> datetime:
        return self.start_utc + timedelta(minutes=self.minutes)

    def hour_key_utc(self) -> str:
        """YYYY-MM-DDTHH — the only hour key any join may use."""
        return self.start_utc.strftime("%Y-%m-%dT%H")


def parse_offset_iso(label: str) -> datetime:
    """Parse a publisher label like '2026-10-26 18:00:00-0500' to aware UTC.

    Python 3.11+ handles the -0500 form natively; we normalise to UTC so the
    rest of the system never sees a local offset again.
    """
    text = label.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M%z", "%Y-%m-%d %H:%M%z"):
        try:
            return datetime.strptime(text, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    raise TimeError(f"cannot parse interval label {label!r}")


def hour_ending_to_start(label: str) -> datetime:
    """EIA-930 style: the stamp is the END of the hour. Move back one hour."""
    return parse_offset_iso(label) - timedelta(hours=1)


def hours_in_local_day(year: int, month: int, day: int, tz_offset_hours: int) -> int:
    """Count hours in a *local* civil day for a fixed-offset zone.

    A fixed-offset model cannot express the DST transition, which is the
    point: the test asserts that a naive 24-hour assumption is wrong for a
    real zone, and therefore that we must key on UTC instead.
    """
    raise NotImplementedError(
        "fixed offsets cannot represent DST; use UTC keys (see hours_in_utc_day)"
    )


def hours_in_utc_day(year: int, month: int, day: int) -> int:
    """UTC days are always 24 hours — which is exactly why we store UTC."""
    start = datetime(year, month, day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return int((end - start).total_seconds() // 3600)


def dst_transition_day_length(year: int, month: int, day: int,
                              tz_name: str = "America/New_York") -> int:
    """Length in hours of a local civil day, honouring DST.

    2026-11-01 in America/New_York is 25 hours long. We expose this so the
    ingestion layer can assert it rather than discover it in production.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    start = datetime(year, month, day, tzinfo=tz)
    end = start + timedelta(days=1)
    delta = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
    return int(delta.total_seconds() // 3600)
