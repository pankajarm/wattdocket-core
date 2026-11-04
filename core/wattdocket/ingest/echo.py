"""EPA ECHO air facilities for NAICS 518210 (data centres).

Responsible for: the SPEC §2 ``Facility`` row and its provenance fields — a
stable ``echo:<RegistryID>`` id, a name, a street address, coordinates and a
status drawn from a closed set. ``capacity_basis`` is written as ``UNKNOWN``
rather than invented, because INV-4 depends on that field being explicit.

Two ECHO quirks are handled here:

* ``get_facilities`` returns *counts*; ``get_facility_info`` returns the rows.
  We call the latter, and refuse a payload whose row count disagrees with the
  count the same payload reports.
* The frozen CSV snapshot in ``data/`` has no ``AIRStatus`` column (the API
  does). Falling back to it is allowed, but it is announced and the status is
  written ``unknown`` — never guessed.

A facility without coordinates is a failure, not a row to skip: the ledger's
value is that every card can be placed on the grid.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .net import (IngestionError, Fetcher, canonical_json, default_fetcher,
                  sha256_bytes, url_with_params)

ECHO_BASE = "https://echodata.epa.gov/echo/air_rest_services.get_facility_info"
ECHO_URL = url_with_params(ECHO_BASE, {
    "output": "JSON",
    "p_ncs": "518210",
    "p_pt": "Y",
})
NAICS = "518210"

# ECHO status text -> the SPEC's closed set. ECHO has no "closed" member in
# that set, so closures land on `unknown` and keep their raw text alongside.
STATUS_MAP = {
    "operating": "operating",
    "under construction": "under_construction",
    "planned facility": "proposed",
    "permitted": "permitted",
    "expanding": "expanding",
    "permanently closed": "unknown",
    "temporarily closed": "unknown",
    "closed": "unknown",
}
DOMAIN_STATUSES = frozenset({
    "operating", "under_construction", "expanding", "proposed", "permitted", "unknown",
})


class EchoError(IngestionError):
    pass


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_status(raw: str | None) -> str:
    key = (raw or "").strip().lower()
    return STATUS_MAP.get(key, "unknown")


def _to_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def facility_from_echo_row(row: dict, *, retrieved_at: str, source_url: str,
                           source_id: str = "epa-echo-air-naics518210") -> dict:
    """One ECHO facility dict -> one ledger ``facility`` row."""
    registry_id = str(row.get("RegistryID") or "").strip()
    if not registry_id:
        raise EchoError(f"ECHO row has no RegistryID: {row!r}", ECHO_URL)
    lat, lon = _to_float(row.get("FacLat")), _to_float(row.get("FacLong"))
    if lat is None or lon is None:
        raise EchoError(
            f"ECHO facility {registry_id} has no coordinates; refusing to write a "
            f"facility that cannot be bound to a grid", ECHO_URL,
        )
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise EchoError(f"ECHO facility {registry_id} has out-of-range coordinates "
                        f"({lat}, {lon})", ECHO_URL)
    status_raw = (row.get("AIRStatus") or "").strip()
    status = map_status(status_raw)
    if status not in DOMAIN_STATUSES:
        raise EchoError(f"status {status!r} is outside the SPEC's closed set", ECHO_URL)
    return {
        "id": f"echo:{registry_id}",
        "name": (row.get("AIRName") or "").strip(),
        "operator": None,
        "naics": (row.get("AIRNAICS") or NAICS).strip() or NAICS,
        "lat": lat,
        "lon": lon,
        "address_line": (row.get("AIRStreet") or "").strip() or None,
        "city": (row.get("AIRCity") or "").strip() or None,
        "state": (row.get("AIRState") or "").strip() or None,
        "zip": (row.get("AIRZip") or "").strip() or None,
        "status": status,
        "status_raw": status_raw or None,
        "status_source_id": source_id,
        "capacity_mw": None,
        "capacity_basis": "UNKNOWN",     # INV-4: explicit, never inferred
        "capacity_source_id": None,
        "source_id": source_id,
        "source_url": source_url,
        "retrieved_at_utc": retrieved_at,
    }


def parse_echo_payload(payload: dict, *, retrieved_at: str | None = None,
                       source_url: str = ECHO_URL) -> list[dict]:
    """Parse the ``get_facility_info`` JSON body into facility rows.

    Refuses when the payload's own ``QueryRows`` count disagrees with the rows
    returned: a truncated or paged response must not become a smaller ledger.
    """
    if not isinstance(payload, dict):
        raise EchoError("ECHO payload is not a JSON object", source_url)
    results = payload.get("Results")
    if not isinstance(results, dict):
        raise EchoError(f"ECHO payload has no Results object: keys={list(payload)}",
                        source_url)
    rows = results.get("Facilities")
    if rows is None:
        message = results.get("Message") or "no Facilities array"
        raise EchoError(f"ECHO returned no facility rows: {message}", source_url)
    declared = results.get("QueryRows")
    if declared is not None:
        try:
            declared_int = int(declared)
        except (TypeError, ValueError):
            declared_int = None
        if declared_int is not None and declared_int != len(rows):
            raise EchoError(
                f"ECHO declared QueryRows={declared_int} but returned {len(rows)} "
                f"rows; refusing a truncated snapshot", source_url)

    stamp = retrieved_at or _now_utc()
    facilities = [facility_from_echo_row(r, retrieved_at=stamp, source_url=source_url)
                  for r in rows]
    return _merge_duplicate_ids(facilities, source_url)


def _merge_duplicate_ids(facilities: list[dict], source_url: str) -> list[dict]:
    """Merge rows that share a RegistryID; refuse if their coordinates differ.

    ECHO's NAICS query returns one row per authorised air *source*, so a campus
    with three permits appears three times under one RegistryID (the frozen
    snapshot is 479 rows / 466 facilities). A facility is the facility, so the
    rows are merged and the extra source ids are kept on the row; two different
    coordinate pairs under one id would mean two facilities, which is a data
    error we refuse rather than paper over.
    """
    merged: dict[str, dict] = {}
    for facility in facilities:
        existing = merged.get(facility["id"])
        if existing is None:
            facility["source_rows"] = 1
            merged[facility["id"]] = facility
            continue
        same_place = (round(existing["lat"], 5) == round(facility["lat"], 5)
                      and round(existing["lon"], 5) == round(facility["lon"], 5))
        if not same_place:
            raise EchoError(
                f"RegistryID {facility['id']} appears at two different coordinate "
                f"pairs; refusing to merge two facilities under one id", source_url)
        existing["source_rows"] = existing.get("source_rows", 1) + 1
    return [merged[key] for key in sorted(merged)]


def facilities_from_csv(path: str | Path, *, retrieved_at: str | None = None,
                        source_url: str = ECHO_URL,
                        source_id: str = "epa-echo-air-naics518210-csv") -> list[dict]:
    """Read the frozen CSV snapshot. No status column: status is ``unknown``."""
    path = Path(path)
    if not path.is_file():
        raise EchoError(f"ECHO CSV snapshot not found: {path}", source_url)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise EchoError(f"ECHO CSV snapshot is empty: {path}", source_url)
    stamp = retrieved_at or _now_utc()
    facilities = [facility_from_echo_row(r, retrieved_at=stamp,
                                         source_url=source_url, source_id=source_id)
                  for r in rows]
    return _merge_duplicate_ids(facilities, source_url)


def load_facilities(fetcher: Fetcher | None = None,
                    csv_fallback: str | Path | None = None,
                    retrieved_at: str | None = None) -> tuple[list[dict], str]:
    """Fetch ECHO facilities; fall back to the frozen CSV only when named.

    Returns ``(facilities, note)``. The note is written into the build log so a
    CSV-derived ledger says so; a missing source raises instead of returning an
    empty list.
    """
    fetcher = fetcher or default_fetcher()
    stamp = retrieved_at or _now_utc()
    try:
        raw = fetcher.get_bytes(ECHO_URL)
    except IngestionError as exc:
        if csv_fallback is None:
            raise
        facilities = facilities_from_csv(csv_fallback, retrieved_at=stamp,
                                         source_url=ECHO_URL)
        return facilities, (f"WARNING: ECHO API fetch failed ({exc}); used the frozen "
                            f"CSV snapshot {csv_fallback} — status is 'unknown' for all rows")
    import json as _json
    payload = _json.loads(raw)
    facilities = parse_echo_payload(payload, retrieved_at=stamp, source_url=ECHO_URL)
    return facilities, (f"ECHO API {ECHO_URL} sha256={sha256_bytes(raw)[:16]}… "
                        f"rows={len(facilities)}")


def facilities_digest(facilities: Iterable[dict]) -> str:
    """Stable digest of the parsed rows — the ledger's facility snapshot id."""
    return sha256_bytes(canonical_json(list(facilities)).encode("utf-8"))
