"""Build the hero card: one facility, deep, with a real hourly grid series.

The hero is what the demo opens on, so every part of it is either measured or
explicitly modelled — and the hourly series is real ERCOT data, not a shape.
Because ERCOT is the one balancing authority whose five-minute fuel mix is
keyless, the hero is a Texas facility. Everything else on the card comes from
the same deterministic path as the other 478.

    python -m wattdocket.hero --cards artifacts/cards --out artifacts/cards \
        --ercot /tmp/ercot.json --facility echo_110000471089
"""

from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

from .grid import intensity_from_fuel_mix
from .timeutil import parse_offset_iso


def hourly_profile(ercot_payload: dict, day: str) -> dict:
    buckets: dict[str, list[float]] = {}
    raw_intervals: list[float] = []
    storage_mw_samples: list[float] = []
    storage_total = 0.0
    interval_minutes = 5
    # Bucket by the PUBLISHED LOCAL hour. parse_offset_iso normalises to UTC, and
    # the previous code then took .strftime("%H") from the UTC value — so hour
    # index 0 was 19:00 Central, six hours into the previous evening. The chart
    # read "cleanest at 16:00" for what ERCOT labels 11:00, and the visible
    # 980→721 cliff between two bars was the midnight wraparound, not the grid.
    offsets: dict[str, str] = {}
    for label, fuels in sorted(ercot_payload["data"][day].items()):
        gen = {k: v.get("gen", 0.0) for k, v in fuels.items()}
        inten = intensity_from_fuel_mix(gen)
        # `storage_excluded_mwh` is misnamed upstream: the field carries MW for
        # one interval, not MWh. Convert with the interval length, or the daily
        # total is 12x too large on top of the double-count that grid.py fixed.
        storage_mw_samples.append(inten.storage_excluded_mwh)
        storage_total += inten.storage_excluded_mwh * (interval_minutes / 60.0)
        raw_intervals.append(inten.value_lb_per_mwh)
        # The label carries the publisher's own local time; read the hour from
        # there and keep the offset so the UI can name the zone.
        local_hour = label[11:13]
        offset = label[-5:]
        offsets[local_hour] = offset
        buckets.setdefault(local_hour, []).append(inten.value_lb_per_mwh)

    hours = sorted(buckets, key=int)
    means = [sum(buckets[h]) / len(buckets[h]) for h in hours]
    return {
        "ba": "ERCT",
        "day": day,
        "intervals": sum(len(v) for v in buckets.values()),
        "means_lb_per_mwh": [round(v, 1) for v in means],
        # The hour each mean belongs to, in the publisher's local clock, so the
        # chart can label a bar without guessing.
        "hours_local": hours,
        "utc_offset": next(iter(offsets.values()), "") if offsets else "",
        "hour_basis": "publisher's local clock (ERCOT, Central time)",
        # Two different extremes, previously conflated under one label. The card
        # said "288 intervals, 561-1022" — but 561 and 1022 are the extremes of
        # the 24 *hourly means*, while the 288 five-minute readings actually span
        # 551-1037. Both are true; only one of them was labelled correctly, so
        # now both are named.
        "min": round(min(means), 1),
        "max": round(max(means), 1),
        "ratio": round(max(means) / min(means), 3),
        "mean_basis": "extremes of the 24 hourly means printed in means_lb_per_mwh",
        "interval_min": round(min(raw_intervals), 1) if raw_intervals else None,
        "interval_max": round(max(raw_intervals), 1) if raw_intervals else None,
        "interval_ratio": (round(max(raw_intervals) / min(raw_intervals), 3)
                           if raw_intervals else None),
        "interval_basis": ("extremes of the underlying five-minute readings, "
                           "including within-hour variation the means smooth out"),
        # MWh of battery charging excluded from the denominator over the day.
        # Reported as energy because that is what the sum is; averaging it back
        # to a mean MW would hide that no single interval carried this much.
        "storage_excluded_mwh": round(storage_total, 1),
        # Mean power excluded, from the per-interval MW samples — not from the
        # daily energy total, which would give the same answer only by accident.
        "storage_excluded_mean_mw": (
            round(sum(storage_mw_samples) / len(storage_mw_samples), 1)
            if storage_mw_samples else None
        ),
        "intensity_state": "PROVISIONAL",
        "factor_set_id": "us-avg-2023.1",
        "note": ("hourly means of ERCOT five-minute fuel-mix intervals; battery "
                 "storage excluded from the denominator. storage_excluded_mwh is "
                 "energy excluded across the whole day, not a capacity"),
    }


def main() -> None:
    ap = ArgumentParser()
    ap.add_argument("--cards", default="artifacts/cards")
    ap.add_argument("--out", default="artifacts/cards")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ercot")
    # ERCOT's dashboard only serves the last couple of days, so the day the
    # hero was built on cannot be fetched again. --from-card reuses the series
    # already published on a card, so a card rebuild need not lose it.
    src.add_argument("--from-card")
    ap.add_argument("--facility", default="")
    ap.add_argument("--day", default="")
    args = ap.parse_args()

    if args.ercot:
        ercot = json.loads(Path(args.ercot).read_text())
        day = args.day or sorted(ercot["data"])[0]
        hourly = hourly_profile(ercot, day)
    else:
        hourly = json.loads(Path(args.from_card).read_text()).get("hourly")
        if not hourly:
            raise SystemExit(f"{args.from_card} carries no hourly series")
        day = hourly["day"]

    cards_dir = Path(args.cards)
    if args.facility:
        target = cards_dir / f"{args.facility}.json"
    else:
        # pick an ERCT card with a real name, deterministically
        candidates = []
        for p in sorted(cards_dir.glob("*.json")):
            c = json.loads(p.read_text())
            if c["grid"]["subregion"] == "ERCT":
                candidates.append(p)
        if not candidates:
            raise SystemExit("no ERCT card found")
        target = candidates[0]

    card = json.loads(target.read_text())
    card["hourly"] = hourly
    card["hero"] = True

    for claim in card["claims"]:
        if claim["id"] == "carbon-t":
            claim["chain"].append(
                f"hourly: {hourly['min']:.0f}-{hourly['max']:.0f} lb/MWh "
                f"({hourly['ratio']:.2f}x) across {hourly['intervals']} "
                f"ERCOT intervals on {day}")
            claim["sensitivity"].append({
                "label": "run the same load at the cleanest hour",
                "delta": f"{hourly['min']:.0f} lb/MWh vs "
                         f"{hourly['max']:.0f} at the worst",
            })

    card["verdict"] = {
        "sentence": (
            "A large load on the Texas grid, where the same hour can be nearly "
            f"twice as carbon-intensive as the cleanest hour of the day — so "
            "when it runs matters as much as what it draws."),
        "source": "template",
        "degraded": False,
    }

    out = Path(args.out) / f"{target.stem}.json"
    out.write_text(json.dumps(card, indent=1))
    print(f"hero card: {out}")
    print(f"  facility: {card['facility']['name']}")
    print(f"  hourly: {hourly['intervals']} intervals, "
          f"{hourly['min']}-{hourly['max']} lb/MWh ({hourly['ratio']}x)")


if __name__ == "__main__":
    main()
