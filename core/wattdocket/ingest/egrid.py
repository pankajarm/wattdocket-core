"""eGRID 2023 subregion sheet -> published CO2e lb/MWh per subregion.

Responsible for: INV-1 (mass is metric tonnes) at the point where it is
easiest to get wrong. The eGRID SRL23 sheet mixes units inside one row:
``SRCO2RTA``-style rate columns are already **lb CO2/MWh**, while ``SRCO2AN``
and ``SRCO2EQA`` are **short tons per year**. The published rate we store is
built from the mass column::

    co2e_rate_lb_per_mwh = SRCO2EQA * 2000 / SRNGENAN

and the annual mass is converted with :func:`wattdocket.units.lb_to_metric_tonnes`
(via the short ton, which is what eGRID published). Two conversions, one
direction, no 2000-vs-2204.62 mixups: the traceable number is always the rate.

Sanity gate: a US subregion rate outside 50–2000 lb/MWh means the units were
misread, so this module refuses rather than emitting it (the ledger's
``--verify`` repeats the check on the artifact).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, BinaryIO

from ..units import UnitError, co2e_rate_from_annual, lb_to_metric_tonnes
from .net import IngestionError, Fetcher

EGRID_URL = ("https://www.epa.gov/system/files/documents/2025-06/"
             "egrid2023_data_rev2.xlsx")
EGRID_SHEET = "SRL23"
DATA_YEAR = 2023
RATE_FLOOR_LB_PER_MWH = 50.0
RATE_CEILING_LB_PER_MWH = 2000.0

HEADER_SCAN_ROWS = 15
REQUIRED_COLUMNS = ("SUBRGN", "SRNAME", "SRNGENAN", "SRCO2AN", "SRCO2EQA")
UNITS_NOTE = ("eGRID SRL23: SRCO2RTA-style rate columns are lb CO2/MWh; "
              "SRCO2AN/SRCO2EQA are SHORT TONS per year")


class EgridError(IngestionError):
    pass


def _numeric(value: Any) -> float | None:
    if value in (None, "", "--", "NA", "NM"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _open_workbook(source: str | Path | bytes | BinaryIO):
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise EgridError(
            "openpyxl is required to read the eGRID workbook "
            "(pip install openpyxl)", EGRID_URL) from exc
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    elif isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise EgridError(f"eGRID workbook not found: {path}", EGRID_URL)
    try:
        return openpyxl.load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:
        raise EgridError(f"cannot open eGRID workbook: {exc}", EGRID_URL) from exc


def _find_header(rows: list[tuple]) -> tuple[int, list[str]]:
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        cells = [str(c).strip().upper() if c is not None else "" for c in row]
        if all(name in cells for name in REQUIRED_COLUMNS):
            return index, cells
    raise EgridError(
        f"could not find the SRL23 header row; looked for {REQUIRED_COLUMNS} "
        f"in the first {HEADER_SCAN_ROWS} rows", EGRID_URL)


def parse_subregion_rates(source: str | Path | bytes | BinaryIO,
                          sheet: str = EGRID_SHEET,
                          warnings: list[str] | None = None) -> dict[str, dict]:
    """Workbook -> ``{subregion: {name, rates, mass, generation}}``.

    Rows are read from the header row eGRID actually uses (row 2), not a fixed
    offset, because EPA ships the header in a different position each revision.
    """
    warnings = warnings if warnings is not None else []
    workbook = _open_workbook(source)
    try:
        if sheet not in workbook.sheetnames:
            raise EgridError(
                f"workbook has no {sheet!r} sheet; sheets={workbook.sheetnames}",
                EGRID_URL)
        worksheet = workbook[sheet]
        rows = list(worksheet.iter_rows(values_only=True))
    finally:
        close = getattr(workbook, "close", None)
        if close:
            close()

    if not rows:
        raise EgridError(f"{sheet} sheet is empty", EGRID_URL)

    header_index, header = _find_header(rows)
    column: dict[str, int | None] = {name: header.index(name) for name in REQUIRED_COLUMNS}
    column["YEAR"] = header.index("YEAR") if "YEAR" in header else None

    rates: dict[str, dict] = {}
    for raw in rows[header_index + 1:]:
        if raw is None:
            continue
        def cell(name: str) -> Any:
            idx = column[name]
            return raw[idx] if idx is not None and len(raw) > idx else None

        code = str(cell("SUBRGN") or "").strip().upper()
        if not code or len(code) > 5 or not code.isalnum():
            continue  # footnote / blank row, not a subregion
        gen = _numeric(cell("SRNGENAN"))
        co2_st = _numeric(cell("SRCO2AN"))
        co2e_st = _numeric(cell("SRCO2EQA"))
        name = str(cell("SRNAME") or "").strip()
        year = _numeric(cell("YEAR")) or DATA_YEAR

        if gen is None or gen <= 0 or co2_st is None or co2e_st is None:
            warnings.append(f"subregion {code} lacks generation or mass; skipped "
                            f"(gen={gen}, co2_st={co2_st}, co2e_st={co2e_st})")
            continue
        try:
            co2_rate = co2e_rate_from_annual(co2_st, gen)
            co2e_rate = co2e_rate_from_annual(co2e_st, gen)
        except UnitError as exc:  # pragma: no cover - guarded above
            raise EgridError(f"subregion {code}: {exc}", EGRID_URL) from exc

        for label, value in (("co2", co2_rate), ("co2e", co2e_rate)):
            if not (RATE_FLOOR_LB_PER_MWH <= value <= RATE_CEILING_LB_PER_MWH):
                raise EgridError(
                    f"subregion {code} {label} rate {value:.1f} lb/MWh is outside "
                    f"{RATE_FLOOR_LB_PER_MWH}-{RATE_CEILING_LB_PER_MWH}; the units "
                    f"in this workbook row were probably misread", EGRID_URL)

        rates[code] = {
            "subregion": code,
            "name": name,
            "data_year": int(year),
            "co2_rate_lb_per_mwh": co2_rate,
            "co2e_rate_lb_per_mwh": co2e_rate,
            "gen_mwh": gen,
            "co2_short_tons_annual": co2_st,
            "co2e_short_tons_annual": co2e_st,
            "co2_metric_tonnes_annual": lb_to_metric_tonnes(co2_st * 2000.0),
            "source_id": "epa-egrid-2023-rev2-srl23",
            "source_url": EGRID_URL,
            "units_note": UNITS_NOTE,
        }

    if not rates:
        raise EgridError(f"no subregion rows parsed from {sheet}", EGRID_URL)
    return rates


def load_subregion_rates(fetcher: Fetcher, warnings: list[str] | None = None,
                         local_path: str | Path | None = None) -> dict[str, dict]:
    """Fetch the workbook (or read a pinned local copy) and parse it."""
    if local_path is not None and Path(local_path).is_file():
        return parse_subregion_rates(Path(local_path), warnings=warnings)
    raw = fetcher.get_bytes(EGRID_URL)
    return parse_subregion_rates(raw, warnings=warnings)


def rate_for(rates: dict[str, dict], subregion: str) -> dict:
    """Look up one subregion, naming the missing key when it is absent."""
    key = (subregion or "").strip().upper()
    try:
        return rates[key]
    except KeyError as exc:
        raise EgridError(
            f"subregion {subregion!r} is not in the eGRID 2023 table "
            f"(known: {sorted(rates)})", EGRID_URL) from exc


def frozen_rates_payload(rates: dict[str, dict], workbook_sha256: str,
                         source_url: str = EGRID_URL) -> dict:
    """The committed, timestamp-free form of the rates (byte-stable)."""
    return {
        "source": {
            "publisher": "US EPA",
            "dataset": "eGRID 2023 rev2",
            "sheet": EGRID_SHEET,
            "data_year": DATA_YEAR,
            "url": source_url,
            "workbook_sha256": workbook_sha256,
            "rate_formula": "SRCO2EQA (short tons/yr) * 2000 / SRNGENAN (MWh)",
            "units_note": UNITS_NOTE,
        },
        "subregions": {
            code: {
                "name": row["name"],
                "co2_rate_lb_per_mwh": round(row["co2_rate_lb_per_mwh"], 3),
                "co2e_rate_lb_per_mwh": round(row["co2e_rate_lb_per_mwh"], 3),
                "gen_mwh": round(row["gen_mwh"], 3),
                "co2e_short_tons_annual": round(row["co2e_short_tons_annual"], 3),
                "co2_metric_tonnes_annual": round(row["co2_metric_tonnes_annual"], 3),
            }
            for code, row in sorted(rates.items())
        },
    }


def load_frozen_rates(path: str | Path) -> dict[str, dict]:
    """Read ``data/egrid_subregion_rates.json`` -> ``{code: row}``."""
    path = Path(path)
    if not path.is_file():
        raise EgridError(f"frozen eGRID rates not found: {path} "
                         f"(create with python -m wattdocket.ingest.egrid --freeze)",
                         EGRID_URL)
    payload = json.loads(path.read_text(encoding="utf-8"))
    subregions = payload.get("subregions") or {}
    if not subregions:
        raise EgridError(f"frozen rates file has no subregions: {path}", EGRID_URL)
    source = payload.get("source", {})
    out: dict[str, dict] = {}
    for code, row in subregions.items():
        value = float(row["co2e_rate_lb_per_mwh"])
        if not (RATE_FLOOR_LB_PER_MWH <= value <= RATE_CEILING_LB_PER_MWH):
            raise EgridError(f"frozen rate for {code} is {value} lb/MWh, outside "
                             f"{RATE_FLOOR_LB_PER_MWH}-{RATE_CEILING_LB_PER_MWH}",
                             EGRID_URL)
        out[code.upper()] = {
            "subregion": code.upper(),
            "name": row["name"],
            "co2_rate_lb_per_mwh": float(row["co2_rate_lb_per_mwh"]),
            "co2e_rate_lb_per_mwh": value,
            "gen_mwh": float(row["gen_mwh"]),
            "co2e_short_tons_annual": float(row.get("co2e_short_tons_annual", 0.0)),
            "co2_metric_tonnes_annual": float(row["co2_metric_tonnes_annual"]),
            "data_year": int(source.get("data_year", DATA_YEAR)),
            "source_id": "epa-egrid-2023-rev2-srl23",
            "source_url": source.get("url", EGRID_URL),
            "workbook_sha256": source.get("workbook_sha256"),
            "units_note": UNITS_NOTE,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    """Freeze the SRL23 rates to JSON (no timestamps, byte-stable output)."""
    import argparse
    import hashlib

    parser = argparse.ArgumentParser(prog="wattdocket.ingest.egrid")
    parser.add_argument("--freeze", action="store_true", required=True,
                        help="write data/egrid_subregion_rates.json")
    parser.add_argument("--workbook", required=True,
                        help="local egrid2023_data_rev2.xlsx")
    parser.add_argument("--out", default="data/egrid_subregion_rates.json")
    args = parser.parse_args(argv)

    raw = Path(args.workbook).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rates = parse_subregion_rates(raw)
    payload = frozen_rates_payload(rates, digest)
    text = json.dumps(payload, indent=1, sort_keys=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"path": str(out), "subregions": len(rates),
                      "workbook_sha256": digest,
                      "bytes": len(text)}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
