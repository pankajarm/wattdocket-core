"""Capture the offline fixtures the tests and ``--fixture-dir`` builds replay.

This is the *only* module in the package that is expected to touch the live
network on a developer's machine, and it writes what it saw:

* ``echo_naics518210_fields.json`` — the ECHO ``get_facility_info`` response
  reduced to the fields the parser reads (all 479 rows, ``QueryRows`` intact).
* ``ercot_fuel_mix.json`` — one ERCOT day, reduced to ``{'gen': MW}`` per fuel.
* ``egrid_srl23.xlsx`` — the eGRID 2023 ``SRL23`` sheet copied verbatim into a
  single-sheet workbook (so a 21 MB download is not committed).
* ``geo_queries.json`` — every geocoder and polygon response the five test
  cities need, keyed by the exact URL :mod:`wattdocket.ingest.geo` builds.
* ``manifest.json`` — the URL -> file map, with the sha256 of each source body.

Usage::

    python -m wattdocket.ingest.capture_fixtures --out core/tests/fixtures \\
        [--day 2026-10-26] [--egrid-workbook /tmp/egrid2023.xlsx]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import echo, egrid, ercot, geo
from .net import UrlFetcher, sha256_bytes

DEFAULT_DAY = "2026-10-26"

# The five verified city lookups (SPEC day-1 acceptance: address -> subregion).
TEST_ADDRESSES = {
    "ashburn": "21715 Filigree Ct, Ashburn, VA 20147",
    "dallas": "2020 N Lamar St, Dallas, TX 75202",
    "santa_clara": "2455 Augustine Dr, Santa Clara, CA 95054",
    "columbus": "1 E Broad St, Columbus, OH 43215",
    "utica": "200 Genesee St, Utica, NY 13502",
}

ECHO_FIELDS = (
    "RegistryID", "AIRName", "AIRStreet", "AIRCity", "AIRState", "AIRZip",
    "FacLat", "FacLong", "AIRStatus", "AIRNAICS", "SourceID",
)
ERCOT_FUEL_KEYS = (
    "Coal and Lignite", "Natural Gas", "Nuclear", "Wind", "Solar", "Hydro",
    "Other", "Power Storage", "Power Storage Charging", "Power Storage Discharging",
)


def reduce_echo(payload: dict) -> tuple[dict, str]:
    results = payload["Results"]
    rows = results["Facilities"]
    reduced = [{field: row.get(field) for field in ECHO_FIELDS} for row in rows]
    body = {
        "Results": {
            "Message": results.get("Message"),
            "QueryRows": results.get("QueryRows", len(reduced)),
            "Facilities": reduced,
        }
    }
    return body, json.dumps(body, sort_keys=True)


def reduce_ercot(payload: dict, day: str) -> tuple[dict, str]:
    intervals = payload["data"][day]
    reduced: dict[str, dict] = {}
    for label in sorted(intervals):
        fuels = intervals[label]
        reduced[label] = {
            fuel: {"gen": fuels[fuel].get("gen", 0.0)}
            for fuel in ERCOT_FUEL_KEYS if fuel in fuels
        }
    body = {"data": {day: reduced}}
    return body, json.dumps(body, sort_keys=True)


def copy_srl23(workbook_path: str | Path, out_path: Path) -> None:
    import openpyxl

    source = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    target = openpyxl.Workbook(write_only=True)
    sheet = target.create_sheet(egrid.EGRID_SHEET)
    try:
        for row in source[egrid.EGRID_SHEET].iter_rows(values_only=True):
            sheet.append(list(row))
    finally:
        source.close()
    target.save(out_path)


def capture(out_dir: str | Path, day: str = DEFAULT_DAY,
            egrid_workbook: str | Path | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fetcher = UrlFetcher()
    entries: list[dict] = []
    summary: dict[str, Any] = {}

    raw = fetcher.get_bytes(echo.ECHO_URL)
    echo_body, echo_canonical = reduce_echo(json.loads(raw))
    (out / "echo_naics518210_fields.json").write_text(
        json.dumps(echo_body, indent=1, sort_keys=True), encoding="utf-8")
    entries.append({
        "url": echo.ECHO_URL,
        "file": "echo_naics518210_fields.json",
        "sha256_source": sha256_bytes(raw),
        "note": "get_facility_info response reduced to parser fields; all rows kept",
    })
    summary["echo_rows"] = len(echo_body["Results"]["Facilities"])
    summary["echo_source_sha256"] = sha256_bytes(raw)

    raw = fetcher.get_bytes(ercot.ERCOT_URL)
    ercot_body, _ = reduce_ercot(json.loads(raw), day)
    (out / "ercot_fuel_mix.json").write_text(
        json.dumps(ercot_body, indent=1, sort_keys=True), encoding="utf-8")
    entries.append({
        "match": "fuel-mix.json",
        "file": "ercot_fuel_mix.json",
        "sha256_source": sha256_bytes(raw),
        "note": f"ERCOT dashboard day {day}, reduced to per-fuel 'gen' MW",
    })
    summary["ercot_day"] = day
    summary["ercot_intervals"] = len(ercot_body["data"][day])

    if egrid_workbook:
        copy_srl23(egrid_workbook, out / "egrid_srl23.xlsx")
        entries.append({
            "match": "egrid2023_data_rev2.xlsx",
            "file": "egrid_srl23.xlsx",
            "note": f"SRL23 sheet copied verbatim from {Path(egrid_workbook).name}",
        })
        summary["egrid_fixture"] = "egrid_srl23.xlsx"

    geo_map: dict[str, Any] = {}
    located: dict[str, dict] = {}
    for key, address in TEST_ADDRESSES.items():
        found = geo.geocode(address, fetcher=fetcher)
        located[key] = found
        geo_map[geo.census_url(address)] = fetcher.get_json(geo.census_url(address))
        geo_map[geo.polygon_url(found["lat"], found["lon"])] = fetcher.get_json(
            geo.polygon_url(found["lat"], found["lon"]))
    (out / "geo_queries.json").write_text(
        json.dumps(geo_map, indent=1, sort_keys=True), encoding="utf-8")
    entries.append({
        "urls_file": "geo_queries.json",
        "note": "Census geocoder + eGRID polygon responses for the five test cities",
    })
    summary["geo"] = {
        key: {
            "address": TEST_ADDRESSES[key],
            "lat": round(found["lat"], 5),
            "lon": round(found["lon"], 5),
            "matched": found["matched_address"],
        }
        for key, found in located.items()
    }

    manifest = {"entries": entries, "captured_at_utc": _now()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    summary["manifest"] = str(out / "manifest.json")
    return summary


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wattdocket.ingest.capture_fixtures")
    parser.add_argument("--out", required=True, help="fixture directory")
    parser.add_argument("--day", default=DEFAULT_DAY, help="ERCOT day to keep")
    parser.add_argument("--egrid-workbook", default=None,
                        help="local eGRID .xlsx to copy the SRL23 sheet from")
    args = parser.parse_args(argv)
    print(json.dumps(capture(args.out, day=args.day,
                             egrid_workbook=args.egrid_workbook), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
