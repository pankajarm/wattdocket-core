"""Fetch and parse Virginia DEQ's issued air permits for data centers.

This page is the single best public asset we found: the Commonwealth publishes a
list of air permits it has issued *specifically for data centers*, each row
linking the actual permit PDF. Two things follow that nothing else gives us:

  1. The document itself — hourly generator limits, tonnage caps, fuel
     restrictions, stack testing — which is what an operator is actually held to.
  2. An authoritative statement of facility type. "Is this building a data
     center?" is answered by the state that permitted it, not by a NAICS code and
     not by us.

    python scripts/fetch_deq_permits.py --out data/deq_air_permits.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import urllib.request
from pathlib import Path

PAGE = ("https://www.deq.virginia.gov/news-info/shortcuts/permits/air/"
        "issued-air-permits-for-data-centers")

# | Site | [**reg**](pdf) | date | program | county | office |
ROW = re.compile(
    r"^\|\s*(?P<site>[^|]+?)\s*\|\s*\[\*\*(?P<reg>[^\]]+)\*\*\]\((?P<url>[^)]+)\)\s*"
    r"\|\s*(?P<date>[0-9/]+)\s*\|\s*(?P<program>[^|]+?)\s*\|\s*(?P<county>[^|]+?)\s*"
    r"\|\s*(?P<office>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)


def fetch(url: str = PAGE) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "wattdocket/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_markdown(md: str) -> list[dict]:
    out = []
    for m in ROW.finditer(md):
        d = m.groupdict()
        out.append({
            "site": d["site"].strip(),
            "registration": d["reg"].strip(),
            "issued": d["date"].strip(),
            "program": d["program"].strip(),
            "county": d["county"].strip(),
            "office": d["office"].strip(),
            "permit_url": d["url"].strip(),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/deq_air_permits.csv")
    ap.add_argument("--source", default="", help="local markdown copy, skips the fetch")
    args = ap.parse_args()

    md = Path(args.source).read_text() if args.source else fetch()
    rows = parse_markdown(md)
    if not rows:
        raise SystemExit("no permit rows parsed — the page layout may have changed")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    sites = {r["site"] for r in rows}
    counties: dict[str, int] = {}
    for r in rows:
        counties[r["county"]] = counties.get(r["county"], 0) + 1
    print(f"permits: {len(rows)} | distinct sites: {len(sites)}")
    print(f"counties: {len(counties)} | top: "
          f"{sorted(counties.items(), key=lambda kv: -kv[1])[:4]}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
