"""Calibrate the emission-factor table against measured CEMS data.

Why this is the right test, and not the one in the spec.

T1 as written wants per-facility actual energy or water, which does not exist in
public data for data centers. But the thing that actually makes our carbon
number uncertain is not the facility — it is the *factor table*: which lb CO2 per
MWh you assign to coal and to gas. Review measured that uncertainty at ~20% of
the level.

CEMS measures CO2 mass and gross load, per unit, per hour. So we can compute the
measured emission factor of real coal units and real gas units and compare them
with the constants our estimator multiplies by. That is a direct test of the one
input that moves the answer.

Two things this test can and cannot say:
  - CAN: "our coal factor of 2,220 lb/MWh is within X% of what coal units
    actually emitted on this day" — a real calibration of the table.
  - CANNOT: produce a whole-grid intensity, because CEMS covers affected fossil
    units only. Nuclear, wind, solar and hydro carry no CEMS mass, so summing
    CEMS alone gives *fossil* intensity, which is much higher than the grid
    average. The distinction is reported, never blurred.

    python scripts/calibrate_camd.py --day 2026-09-15 --state TX
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "core"))

BASE = "https://api.epa.gov/easey/streaming-services"
CACHE = ROOT / "data" / "camd"
LB_PER_SHORT_TON = 2000.0


def key() -> str:
    k = os.environ.get("CAMD_API_KEY")
    if k:
        return k.strip()
    env = pathlib.Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("CAMD_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no CAMD_API_KEY in env or the repo .env")


def fetch_day(day: str, state: str, per_page: int = 5000) -> list[dict]:
    """Every hourly row for one state-day, paginated, cached, rate-limit aware."""
    cache = CACHE / f"{state}_{day}.json"
    if cache.exists():
        return json.loads(cache.read_text())

    rows: list[dict] = []
    page = 1
    while True:
        params = {"beginDate": day, "endDate": day, "stateCode": state,
                  "page": page, "perPage": per_page}
        url = f"{BASE}/emissions/apportioned/hourly?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"x-api-key": key(),
                                                   "accept": "application/json"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    batch = json.loads(r.read().decode())
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    wait = 20 * (attempt + 1)
                    print(f"    rate limited, waiting {wait}s", flush=True)
                    time.sleep(wait)
                    continue
                body = exc.read().decode()[:200]
                raise SystemExit(f"HTTP {exc.code}: {body}")
        else:
            raise SystemExit("rate limited five times; try again later")

        if not batch:
            break
        rows.extend(batch)
        print(f"    page {page}: {len(batch)} rows (total {len(rows)})", flush=True)
        if len(batch) < per_page:
            break
        page += 1
        time.sleep(3)                       # be a good citizen

    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows))
    return rows


def fuel_group(row: dict) -> str | None:
    """Coal / gas / oil, from CAMD's own fuel description.

    Anything we cannot place confidently returns None and is excluded: a unit
    whose fuel is ambiguous would silently contaminate the factor it is used to
    validate.
    """
    primary = (row.get("primaryFuelInfo") or "").lower()
    secondary = (row.get("secondaryFuelInfo") or "").lower()
    text = f"{primary} {secondary}"
    if "coal" in text:
        return "coal"
    if "natural gas" in text or "pipeline" in text or text.strip() == "gas":
        return "gas"
    if "oil" in text or "diesel" in text or "residual" in text:
        return "oil"
    return None


def measured(rows: list[dict]) -> dict:
    """Sum mass and load by fuel, then divide. Only rows with both fields."""
    acc: dict[str, dict[str, float]] = defaultdict(lambda: {"tonnes": 0.0, "mwh": 0.0, "n": 0})
    hourly: dict[int, dict[str, float]] = defaultdict(lambda: {"tonnes": 0.0, "mwh": 0.0})
    skipped = 0
    for r in rows:
        mass = r.get("co2Mass")
        load = r.get("grossLoad")
        hour = r.get("hour")
        if not isinstance(mass, (int, float)) or not isinstance(load, (int, float)):
            skipped += 1
            continue
        if load <= 0 or mass < 0 or not isinstance(hour, int):
            skipped += 1
            continue
        group = fuel_group(r)
        if group is None:
            skipped += 1
            continue
        acc[group]["tonnes"] += mass
        acc[group]["mwh"] += load
        acc[group]["n"] += 1
        hourly[hour]["tonnes"] += mass
        hourly[hour]["mwh"] += load

    factors = {}
    for group, a in acc.items():
        if a["mwh"] > 0:
            factors[group] = {
                "lb_per_mwh": a["tonnes"] * LB_PER_SHORT_TON / a["mwh"],
                "mwh": a["mwh"],
                "tonnes": a["tonnes"],
                "unit_hours": a["n"],
            }
    hourly_intensity = {
        h: v["tonnes"] * LB_PER_SHORT_TON / v["mwh"]
        for h, v in hourly.items() if v["mwh"] > 0
    }
    return {"by_fuel": factors, "hourly": hourly_intensity,
            "rows": len(rows), "skipped": skipped}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-09-15")
    ap.add_argument("--state", default="TX")
    ap.add_argument("--out", default="artifacts/camd_calibration.json")
    args = ap.parse_args()

    print(f"CAMD hourly {args.state} {args.day}")
    rows = fetch_day(args.day, args.state)
    print(f"rows: {len(rows)}")

    m = measured(rows)
    print(f"usable unit-hours: {m['rows'] - m['skipped']} "
          f"(skipped {m['skipped']}: missing mass, zero load, or unknown fuel)")
    print()

    from wattdocket.grid import DEFAULT_FACTOR_SET
    table = DEFAULT_FACTOR_SET
    # The factor set is a fuel -> lb/MWh dict. Missing keys stay None so the
    # comparison prints an em dash rather than inventing a target.
    ours = {"coal": table.lb_per_mwh.get("coal"),
            "gas": table.lb_per_mwh.get("gas"),
            "oil": table.lb_per_mwh.get("oil") or table.lb_per_mwh.get("petroleum")}

    print("MEASURED EMISSION FACTOR BY FUEL (CEMS, this day)")
    print(f"{'fuel':6s} {'measured':>12s} {'table':>10s} {'delta':>9s} {'MWh':>12s} {'units-h':>8s}")
    for group in ("coal", "gas", "oil"):
        f = m["by_fuel"].get(group)
        if not f:
            continue
        mine = ours.get(group)
        delta = f"{f['lb_per_mwh'] / mine - 1:+.1%}" if mine else "—"
        print(f"{group:6s} {f['lb_per_mwh']:12,.0f} "
              f"{(f'{mine:,.0f}' if mine else '—'):>10s} {delta:>9s} "
              f"{f['mwh']:12,.0f} {f['unit_hours']:8,}")

    h = m["hourly"]
    if h:
        lo, hi = min(h.values()), max(h.values())
        print()
        print(f"FOSSIL-ONLY INTENSITY, by hour: {lo:,.0f} – {hi:,.0f} lb/MWh "
              f"({hi / lo:.2f}x)")
        print("  NOTE: this is the intensity of CEMS-covered fossil units, not the")
        print("  grid. Zero-carbon generation carries no CEMS mass and is absent,")
        print("  so the grid figure is lower and cannot be derived from this alone.")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "day": args.day, "state": args.state,
        "rows": len(rows),
        "usable_unit_hours": m["rows"] - m["skipped"],
        "by_fuel": m["by_fuel"],
        "hourly_fossil_intensity": {str(k): v for k, v in sorted(h.items())},
        "table": dict(ours, factor_set_id=table.id),
        "source": "EPA CAMD/easey hourly, apportioned",
    }, indent=1))
    print(f"\nwrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
