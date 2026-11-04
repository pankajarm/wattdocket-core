"""Probe the CAMD/easey API: what's there, how big, how recent.

Answers three questions before building anything:
  1. which date range actually has data
  2. whether hourly rows carry both co2Mass and grossLoad (both are required to
     compute an intensity; a mass without a load is not a rate)
  3. how many pages a single day of one state costs
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.epa.gov/easey/streaming-services"


def key() -> str:
    k = os.environ.get("CAMD_API_KEY")
    if k:
        return k.strip()
    env = pathlib.Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("CAMD_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no CAMD_API_KEY")


def get(path: str, params: dict) -> tuple[int, list]:
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-api-key": key(),
                                               "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "[]")


def coverage(day: str, state: str = "TX") -> dict:
    status, rows = get("emissions/apportioned/hourly",
                       {"beginDate": day, "endDate": day, "stateCode": state,
                        "page": 1, "perPage": 5000})
    if status != 200:
        return {"day": day, "status": status, "rows": 0, "error": str(rows)[:120]}
    usable = [r for r in rows
              if isinstance(r.get("co2Mass"), (int, float))
              and isinstance(r.get("grossLoad"), (int, float))
              and r["grossLoad"] > 0]
    return {"day": day, "status": status, "rows": len(rows),
            "usable": len(usable), "units": len({r["unitId"] for r in usable})}


def main() -> None:
    days = sys.argv[1:] or ["2025-07-01", "2025-08-15", "2025-12-15",
                            "2026-03-15", "2026-06-15", "2026-09-15",
                            "2026-10-26"]
    for d in days:
        c = coverage(d)
        print(f"{d}: {c}")


if __name__ == "__main__":
    main()
