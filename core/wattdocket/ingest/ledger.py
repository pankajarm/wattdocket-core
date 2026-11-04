"""Build one read-only ``ledger.sqlite`` from the five public sources.

Responsible for: the artifact everything else reads. It carries the SPEC §2
tables it owns — ``facility``, ``grid_binding``, ``interval_reading``,
``factor_set``, ``subregion_rate`` — with provenance columns on every row, so
a number can be walked back to a URL and a retrieval time.

Determinism is a requirement, not a nicety:

* rows are inserted in sorted primary-key order;
* the database is written to a sibling temp file and atomically renamed, so a
  failed build never leaves a half-ledger;
* ``PRAGMA journal_mode=DELETE`` (no WAL sidecars to drift out of sync);
* JSON columns are dumped with ``sort_keys=True``.

``--verify`` re-asserts on the finished file what the builder promises: row
counts, coordinates on every facility, subregion rates in 50–2000 lb/MWh, no
negative generation, UTC interval keys, and a factor set behind every
PROVISIONAL intensity (INV-6).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..grid import DEFAULT_FACTOR_SET, DEFAULT_FACTORS_LB_PER_MWH
from . import echo, egrid, ercot, geo
from .net import Fetcher, canonical_json, default_fetcher, sha256_bytes

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ECHO_CSV = REPO_ROOT / "data" / "echo_datacenters_naics518210.csv"
LEDGER_VERSION = "wattdocket-ledger-0.1"

SCHEMA = """
CREATE TABLE facility (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    operator           TEXT,
    naics              TEXT,
    lat                REAL NOT NULL,
    lon                REAL NOT NULL,
    address_line       TEXT,
    city               TEXT,
    state              TEXT,
    zip                TEXT,
    status             TEXT NOT NULL CHECK (status IN
                        ('operating','under_construction','expanding','proposed',
                         'permitted','unknown')),
    status_raw         TEXT,
    status_source_id   TEXT,
    capacity_mw        REAL,
    capacity_basis     TEXT NOT NULL CHECK (capacity_basis IN
                        ('IT_MW','GRID_MW','UNKNOWN')),
    capacity_source_id TEXT,
    source_id          TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    retrieved_at_utc   TEXT NOT NULL
);

CREATE TABLE grid_binding (
    facility_id         TEXT PRIMARY KEY,
    utility             TEXT,
    balancing_authority TEXT,
    egrind_subregion    TEXT,
    method              TEXT NOT NULL CHECK (method IN
                         ('point_in_polygon','operator_filing','manual')),
    lat                 REAL NOT NULL,
    lon                 REAL NOT NULL,
    resolved            INTEGER NOT NULL CHECK (resolved IN (0,1)),
    note                TEXT,
    source_id           TEXT NOT NULL,
    source_url          TEXT NOT NULL,
    resolved_at_utc     TEXT NOT NULL
);

CREATE TABLE factor_set (
    id                TEXT PRIMARY KEY,
    published_by      TEXT NOT NULL,
    effective_date    TEXT NOT NULL,
    lb_per_mwh_by_fuel TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    url               TEXT NOT NULL
);

CREATE TABLE subregion_rate (
    subregion                 TEXT PRIMARY KEY,
    name                      TEXT NOT NULL,
    data_year                 INTEGER NOT NULL,
    co2_rate_lb_per_mwh       REAL NOT NULL,
    co2e_rate_lb_per_mwh      REAL NOT NULL,
    gen_mwh                   REAL NOT NULL,
    co2_short_tons_annual     REAL NOT NULL,
    co2_metric_tonnes_annual  REAL NOT NULL,
    source_id                 TEXT NOT NULL,
    source_url                TEXT NOT NULL,
    retrieved_at_utc          TEXT NOT NULL
);

CREATE TABLE interval_reading (
    ba_code                    TEXT NOT NULL,
    interval_start_utc         TEXT NOT NULL,
    interval_minutes           INTEGER NOT NULL CHECK (interval_minutes > 0),
    label                      TEXT NOT NULL,
    hour_convention            TEXT NOT NULL CHECK (hour_convention IN ('START','END')),
    gen_mwh_by_fuel            TEXT NOT NULL,
    total_gen_mwh              REAL NOT NULL CHECK (total_gen_mwh >= 0),
    intensity_lb_co2e_per_mwh  REAL,
    intensity_state            TEXT NOT NULL CHECK (intensity_state IN
                                ('PROVISIONAL','SETTLED')),
    factor_set_id              TEXT,
    storage_excluded_mwh       REAL NOT NULL,
    source_id                  TEXT NOT NULL,
    source_url                 TEXT NOT NULL,
    retrieved_at_utc           TEXT NOT NULL,
    PRIMARY KEY (ba_code, interval_start_utc)
);

CREATE INDEX idx_interval_start ON interval_reading (interval_start_utc);
CREATE INDEX idx_facility_state ON facility (state);
CREATE INDEX idx_binding_subregion ON grid_binding (egrind_subregion);
"""

FACILITY_COLUMNS = ("id", "name", "operator", "naics", "lat", "lon", "address_line",
                    "city", "state", "zip", "status", "status_raw", "status_source_id",
                    "capacity_mw", "capacity_basis", "capacity_source_id", "source_id",
                    "source_url", "retrieved_at_utc")
BINDING_COLUMNS = ("facility_id", "utility", "balancing_authority", "egrind_subregion",
                   "method", "lat", "lon", "resolved", "note", "source_id",
                   "source_url", "resolved_at_utc")
FACTOR_SET_COLUMNS = ("id", "published_by", "effective_date", "lb_per_mwh_by_fuel",
                      "sha256", "url")
SUBREGION_COLUMNS = ("subregion", "name", "data_year", "co2_rate_lb_per_mwh",
                     "co2e_rate_lb_per_mwh", "gen_mwh", "co2_short_tons_annual",
                     "co2_metric_tonnes_annual", "source_id", "source_url",
                     "retrieved_at_utc")
INTERVAL_COLUMNS = ("ba_code", "interval_start_utc", "interval_minutes", "label",
                    "hour_convention", "gen_mwh_by_fuel", "total_gen_mwh",
                    "intensity_lb_co2e_per_mwh", "intensity_state", "factor_set_id",
                    "storage_excluded_mwh", "source_id", "source_url",
                    "retrieved_at_utc")

JSON_COLUMNS = {"lb_per_mwh_by_fuel", "gen_mwh_by_fuel"}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def factor_set_row(factor_set=DEFAULT_FACTOR_SET) -> dict:
    """The pinned fuel -> lb CO2/MWh table, hashed so a change is visible."""
    table = dict(sorted(factor_set.lb_per_mwh.items()))
    return {
        "id": factor_set.id,
        "published_by": factor_set.published_by,
        "effective_date": factor_set.effective_date,
        "lb_per_mwh_by_fuel": table,
        "sha256": sha256_bytes(canonical_json(table).encode("utf-8")),
        "url": factor_set.url,
    }


def _insert(conn: sqlite3.Connection, table: str, columns: Iterable[str],
            rows: list[dict]) -> None:
    columns = tuple(columns)
    placeholders = ", ".join("?" for _ in columns)
    statement = (f"INSERT INTO {table} ({', '.join(columns)}) "
                 f"VALUES ({placeholders})")
    payload = []
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if column in JSON_COLUMNS and value is not None:
                value = json.dumps(value, sort_keys=True, separators=(",", ":"))
            values.append(value)
        payload.append(tuple(values))
    conn.executemany(statement, payload)


def build(out_path: str | Path, *, fixture_dir: str | Path | None = None,
          echo_csv: str | Path | None = None,
          egrid_local: str | Path | None = None,
          ercot_day: str | None = None,
          bind: bool = True, bind_limit: int | None = None,
          bind_addresses: Iterable[str] = (),
          fetcher: Fetcher | None = None,
          log: list[str] | None = None,
          retrieved_at: str | None = None) -> dict:
    """Fetch, normalise, write the ledger. Returns counts plus the build log."""
    log = log if log is not None else []
    fetcher = fetcher or default_fetcher(fixture_dir)
    stamp = retrieved_at or _now_utc()
    out_path = Path(out_path)

    facilities, echo_note = echo.load_facilities(
        fetcher, csv_fallback=echo_csv if echo_csv is not None else DEFAULT_ECHO_CSV,
        retrieved_at=stamp)
    log.append(f"echo: {echo_note}")
    facilities.sort(key=lambda f: f["id"])

    egrid_warnings: list[str] = []
    rates = egrid.load_subregion_rates(fetcher, warnings=egrid_warnings,
                                       local_path=egrid_local)
    log.extend(f"egrid: {w}" for w in egrid_warnings)
    rate_rows = []
    for code in sorted(rates):
        row = dict(rates[code])
        row["retrieved_at_utc"] = stamp
        rate_rows.append(row)
    log.append(f"egrid: {len(rate_rows)} subregions from {egrid.EGRID_SHEET}")

    intervals = ercot.load_intervals(fetcher, day=ercot_day, retrieved_at=stamp)
    log.append(f"ercot: {len(intervals)} intervals "
               f"({intervals[0]['interval_start_utc']} → "
               f"{intervals[-1]['interval_start_utc']}, {ercot.BA_CODE})")

    bindings: list[dict] = []
    if bind:
        targets = facilities[:bind_limit] if bind_limit is not None else facilities
        for facility in targets:
            bindings.append(geo.binding_for_facility(facility, fetcher=fetcher,
                                                     resolved_at=stamp))
        for address in bind_addresses:
            bindings.append(geo.binding_for_address(address, fetcher=fetcher,
                                                    resolved_at=stamp))
        unresolved = [b["facility_id"] for b in bindings if not b["resolved"]]
        log.append(f"geo: {len(bindings)} bindings, {len(unresolved)} outside eGRID "
                   f"coverage")
        if unresolved:
            log.append("geo: unresolved -> " + ", ".join(sorted(unresolved)[:10]) +
                       (" …" if len(unresolved) > 10 else ""))
    bindings.sort(key=lambda b: b["facility_id"])
    intervals.sort(key=lambda r: (r["ba_code"], r["interval_start_utc"]))

    conn = _connect_for_write(out_path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("BEGIN")
        _insert(conn, "facility", FACILITY_COLUMNS, facilities)
        _insert(conn, "factor_set", FACTOR_SET_COLUMNS, [factor_set_row()])
        _insert(conn, "subregion_rate", SUBREGION_COLUMNS, rate_rows)
        _insert(conn, "interval_reading", INTERVAL_COLUMNS, intervals)
        if bindings:
            _insert(conn, "grid_binding", BINDING_COLUMNS, bindings)
        conn.commit()
        counts = _counts(conn)
    finally:
        conn.close()
    _finish(out_path)

    counts["ledger_version"] = LEDGER_VERSION
    counts["built_at_utc"] = stamp
    counts["log"] = log
    return counts


def _connect_for_write(out_path: Path) -> sqlite3.Connection:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    for stale in (tmp_path, tmp_path.with_name(tmp_path.name + "-journal")):
        if stale.exists():
            stale.unlink()
    conn = sqlite3.connect(tmp_path)
    conn.execute("PRAGMA journal_mode=DELETE")   # WAL off, by contract
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _finish(out_path: Path) -> None:
    """Rename the finished temp database into place, atomically."""
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    os.replace(tmp_path, out_path)


def _counts(conn: sqlite3.Connection) -> dict:
    tables = ("facility", "grid_binding", "interval_reading", "factor_set",
              "subregion_rate")
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def verify(path: str | Path, expected_facilities: int | None = None,
           require_binding: bool = True) -> tuple[bool, dict]:
    """Assert the artifact's promises. Returns ``(ok, report)``.

    ``require_binding`` mirrors the builder: a ``--no-bind`` build is legitimate
    (the SQLite lane is optional today), so its absence of ``grid_binding`` rows
    is only a failure when bindings were supposed to be there.
    """
    path = Path(path)
    report: dict[str, Any] = {"path": str(path), "problems": [], "counts": {},
                              "subregion_rates": {}, "storage_excluded_mwh": 0.0}
    problems: list[str] = report["problems"]

    if not path.is_file():
        problems.append(f"ledger not found: {path}")
        return False, report

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        present = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"facility", "grid_binding", "interval_reading", "factor_set",
                    "subregion_rate"}
        missing = sorted(required - present)
        if missing:
            problems.append(f"missing tables: {missing}")
            return False, report

        report["counts"] = _counts(conn)
        counts = report["counts"]
        if counts["facility"] == 0:
            problems.append("facility table is empty")
        if counts["factor_set"] == 0:
            problems.append("factor_set table is empty (INV-6 needs a factor set)")
        if counts["subregion_rate"] < 20:
            problems.append(f"only {counts['subregion_rate']} subregion rates; "
                            f"eGRID 2023 publishes 27")
        if counts["interval_reading"] == 0:
            problems.append("interval_reading table is empty")
        if counts["grid_binding"] == 0 and require_binding:
            problems.append("grid_binding table is empty")
        if expected_facilities is not None and counts["facility"] != expected_facilities:
            problems.append(f"expected {expected_facilities} facilities, "
                            f"found {counts['facility']}")

        # Every facility is plottable.
        for fac_id, lat, lon in conn.execute("SELECT id, lat, lon FROM facility"):
            if lat is None or lon is None:
                problems.append(f"facility {fac_id} has no coordinates")
            elif not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                problems.append(f"facility {fac_id} has out-of-range coordinates "
                                f"({lat}, {lon})")

        # Rates are physically plausible and the metric mass is not short tons.
        for code, co2, co2e, st_tons, metric in conn.execute(
                "SELECT subregion, co2_rate_lb_per_mwh, co2e_rate_lb_per_mwh, "
                "co2_short_tons_annual, co2_metric_tonnes_annual FROM subregion_rate"):
            report["subregion_rates"][code] = round(co2e, 3)
            for label, value in (("co2", co2), ("co2e", co2e)):
                if not (egrid.RATE_FLOOR_LB_PER_MWH <= value
                        <= egrid.RATE_CEILING_LB_PER_MWH):
                    problems.append(f"subregion {code} {label} rate {value} lb/MWh is "
                                    f"outside 50-2000")
            if metric >= st_tons:
                problems.append(f"subregion {code} metric tonnes ({metric}) >= short "
                                f"tons ({st_tons}); mass units were inverted (INV-1)")

        # Intervals: UTC keys, no negative generation, factor set behind each rate.
        factor_ids = {row[0] for row in conn.execute("SELECT id FROM factor_set")}
        for ba, start, total, state, factor_id, storage in conn.execute(
                "SELECT ba_code, interval_start_utc, total_gen_mwh, intensity_state, "
                "factor_set_id, storage_excluded_mwh FROM interval_reading"):
            if total is None or total < 0:
                problems.append(f"interval {ba} {start} has negative generation {total}")
            if not start.endswith("Z"):
                problems.append(f"interval {ba} {start} is not a UTC key")
            if state == "PROVISIONAL" and factor_id not in factor_ids:
                problems.append(f"interval {ba} {start} is PROVISIONAL without a factor "
                                f"set (INV-6)")
            if state == "SETTLED" and factor_id is None:
                problems.append(f"interval {ba} {start} is SETTLED with no factor set")
            report["storage_excluded_mwh"] += float(storage or 0.0)

        # No orphan bindings (address-prefixed bindings stand alone by design).
        for (fac_id,) in conn.execute(
                "SELECT facility_id FROM grid_binding WHERE facility_id NOT LIKE "
                "'address:%'"):
            exists = conn.execute("SELECT 1 FROM facility WHERE id = ?",
                                  (fac_id,)).fetchone()
            if not exists:
                problems.append(f"grid_binding references unknown facility {fac_id}")
    finally:
        conn.close()

    report["storage_excluded_mwh"] = round(report["storage_excluded_mwh"], 3)
    return (not problems), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wattdocket.ingest.ledger",
        description="Build (or verify) the WattDocket ledger.sqlite")
    parser.add_argument("--out", required=True, help="path to write ledger.sqlite")
    parser.add_argument("--fixture-dir", default=None,
                        help="offline mode: directory of captured responses "
                             "(see capture_fixtures.py)")
    parser.add_argument("--echo-csv", default=str(DEFAULT_ECHO_CSV),
                        help="frozen ECHO snapshot used only when the API fails")
    parser.add_argument("--egrid-workbook", default=None,
                        help="pinned local eGRID .xlsx instead of downloading")
    parser.add_argument("--ercot-day", default=None,
                        help="YYYY-MM-DD inside the ERCOT payload (default: latest)")
    parser.add_argument("--no-bind", action="store_true",
                        help="skip point-in-polygon grid binding")
    parser.add_argument("--bind-limit", type=int, default=None,
                        help="bind only the first N facilities (sorted by id)")
    parser.add_argument("--bind-address", action="append", default=[],
                        help="also bind a raw address (repeatable)")
    parser.add_argument("--expect-facilities", type=int, default=None,
                        help="fail --verify unless this many facilities exist")
    parser.add_argument("--allow-missing-binding", action="store_true",
                        help="with --verify, accept a ledger built with --no-bind")
    parser.add_argument("--verify", action="store_true",
                        help="verify an existing ledger instead of building one")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.verify:
        ok, report = verify(args.out, expected_facilities=args.expect_facilities,
                            require_binding=not args.allow_missing_binding)
        print(json.dumps({k: v for k, v in report.items() if k != "log"},
                         indent=2, sort_keys=True))
        if not ok:
            print(f"VERIFY FAILED: {len(report['problems'])} problem(s)", file=sys.stderr)
            for problem in report["problems"]:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print("VERIFY OK")
        return 0

    result = build(
        args.out,
        fixture_dir=args.fixture_dir,
        echo_csv=args.echo_csv,
        egrid_local=args.egrid_workbook,
        ercot_day=args.ercot_day,
        bind=not args.no_bind,
        bind_limit=args.bind_limit,
        bind_addresses=args.bind_address,
    )
    if not args.quiet:
        for line in result["log"]:
            print(line)
        counts = {k: v for k, v in result.items() if k != "log"}
        print(json.dumps(counts, indent=2, sort_keys=True))
    ok, report = verify(args.out, expected_facilities=args.expect_facilities,
                        require_binding=not (args.no_bind or args.allow_missing_binding))
    if not ok:
        print(f"VERIFY FAILED after build: {report['problems']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
