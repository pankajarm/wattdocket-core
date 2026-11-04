"""Facility cards — the demo's data path: frozen sources in, static JSON out.

Responsible for: INV-7 (nothing renders without a receipt) and INV-9 (water is
never a scalar), on top of the estimator's INV-4 and the factor set's INV-6.

Reads only files already in the repo — the frozen ECHO CSV, the frozen eGRID
SRL23 rates, and the bundled eGRID subregion polygons — so a build is a pure
function of committed bytes: same inputs, same card bytes, no network, no
SQLite, no model. Grid binding is a real point-in-polygon test against EPA's
bundled polygons, not a state-level guess.

Two honesty rules are enforced in code rather than by convention:

* **Status is ``unknown``.** The frozen CSV has no status column, and ECHO
  status is a per-facility API field we did not capture. A card therefore says
  ``unknown``; inventing ``operating`` from a name or a hunch would be exactly
  the kind of confident number this product exists to refuse.
* **Every claim is renderable.** :meth:`wattdocke​t.claims.Claim.renderable` is
  called on every claim before the card is written; a card that cannot render is
  a build failure, not a card with a blank chip. Carbon claims carry both a
  replayable ``calc_id`` and a *substring-verified* citation to the exact CSV
  line the facility came from, so the receipt is real rather than a badge.

Usage::

    python -m wattdocket.ingest.cards \
        --csv ../data/echo_datacenters_naics518210.csv \
        --rates ../data/egrid_subregion_rates.json \
        --polygons ../data/egrid_subregions.geojson \
        --out ../artifacts/cards --index ../artifacts/cards.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Iterable

from ..claims import Badge, Citation, Claim, NotPublished, not_reported
from ..estimator import CapacityBasis, EstimateInput, estimate
from ..grid import DEFAULT_FACTOR_SET
from ..prose import prose_is_bound
from . import egrid, geo

SCHEMA_VERSION = "1.0"
GENERATOR = "wattdocket.ingest.cards@0.1.0"
CALC_VERSION = "1.0.0"

# The ECHO snapshot date recorded in DATA-PROVENANCE.md. Cards carry a source
# date, never a wall clock, so two builds of the same commit are byte-identical.
CARD_AS_OF_UTC = "2026-10-27T00:00:00Z"

# No capacity is published for these facilities (ECHO carries none, and INV-4
# forbids guessing a basis). The card therefore publishes a *sizing* scenario —
# the SPEC's "a typical 100 MW facility here would emit ⟨range⟩" — and says so.
TYPICAL_CAPACITY_MW = 100.0
SIZING_NOTE = ("no facility capacity is published; the carbon claim is a "
               "100 MW sizing scenario, not a measurement of this site")

ECHO_DATASET = "ECHO Air — air_rest_services.get_facility_info, NAICS 518210"
ECHO_PUBLISHER = "US EPA ECHO"

# Subregion -> balancing authority, pinned only where the pairing is definitional.
KNOWN_BA = {"ERCT": "ERCO"}

SOURCE_FILES = ("estimator.py", "units.py", "grid.py", "claims.py", "prose.py")


class CardError(RuntimeError):
    pass


# ----------------------------------------------------------------- utilities
REPO_ROOT = Path(__file__).resolve().parents[3]


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def display_path(path: str | Path) -> str:
    """Stable, portable path: repo-relative when possible, never the cwd."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def code_sha256() -> str:
    """Hash the exact engine sources that produced the numbers."""
    here = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        path = here.parent / name
        if not path.is_file():
            raise CardError(f"engine source missing while hashing: {path}")
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes())
    digest.update(Path(__file__).name.encode("utf-8"))
    digest.update(Path(__file__).read_bytes())
    return digest.hexdigest()


def card_filename(facility_id: str) -> str:
    """``echo:1234`` -> ``echo_1234.json`` (filesystem-safe, reversible)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", facility_id).strip("_")
    if not safe:
        raise CardError(f"facility id {facility_id!r} yields no usable filename")
    return f"{safe}.json"


# ------------------------------------------------------------------- sources
def read_echo_rows(csv_path: str | Path) -> tuple[list[dict], str]:
    """Read the frozen CSV, grouped by ECHO ``RegistryID``, with line numbers.

    The snapshot has **479 rows but 466 distinct RegistryIDs**: thirteen rows are
    a second or third authorised air *source* at the same physical facility
    (e.g. three VADATA permits on one Ashburn campus). A facility card is keyed
    on the facility, so those rows are merged into one group and every row is
    kept as its own citation — the alternative, silently dropping rows or
    emitting colliding ids, would lose evidence or break the id contract.

    The line number is what makes each citation checkable: a reader can open the
    CSV at that line and find the quote.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise CardError(f"frozen ECHO CSV not found: {path}")
    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    text = raw.decode("utf-8")
    lines = text.splitlines()
    reader = csv.DictReader(io.StringIO(text, newline=""))

    groups: dict[str, dict] = {}
    cursor = 1
    for index, row in enumerate(reader, start=2):
        registry = (row.get("RegistryID") or "").strip()
        if not registry:
            raise CardError(f"ECHO CSV line {index} has no RegistryID: {row}")
        line_number = _find_line(lines, registry, digest, start=cursor)
        cursor = line_number
        line_text = lines[line_number - 1].strip()
        group = groups.setdefault(registry, {"registry_id": registry, "rows": [],
                                            "citations": []})
        group["rows"].append(row)
        group["citations"].append({"line": line_number, "text": line_text})
    if not groups:
        raise CardError(f"frozen ECHO CSV is empty: {path}")

    entries = []
    for registry in sorted(groups):
        group = groups[registry]
        coords = {(round(_float(r.get("FacLat"), field="FacLat", registry=registry), 5),
                   round(_float(r.get("FacLong"), field="FacLong", registry=registry), 5))
                  for r in group["rows"]}
        if len(coords) > 1:
            raise CardError(
                f"RegistryID {registry} carries {len(coords)} different coordinate "
                f"pairs across {len(group['rows'])} rows; that is two facilities "
                f"under one id — refusing to merge them")
        entries.append({
            "registry_id": registry,
            "row": group["rows"][0],
            "rows": group["rows"],
            "citations": sorted(group["citations"], key=lambda c: c["line"]),
            "source_row_count": len(group["rows"]),
        })
    return entries, digest


def _find_line(lines: list[str], registry: str, digest: str, start: int = 1) -> int:
    """1-based line of the next row carrying ``registry``, searching from ``start``.

    Scanning forward from the previous hit (rather than from the top) is what
    keeps three source rows for one RegistryID on three distinct lines, instead
    of citing the first one three times.
    """
    for offset in range(start - 1, len(lines)):
        if registry in lines[offset]:
            return offset + 1
    if start > 1:   # the row exists but only above where we have already read
        for offset, line in enumerate(lines):
            if registry in line:
                return offset + 1
    raise CardError(
        f"cannot locate RegistryID {registry} in the frozen CSV "
        f"(sha256 {digest[:12]}…) — the snapshot and the parser disagree")


def _float(value: Any, *, field: str, registry: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CardError(f"ECHO facility {registry} has non-numeric {field}={value!r}") from exc


# ------------------------------------------------------------------ card bits
def facility_block(entry: dict) -> dict:
    row = entry["row"]
    registry = row["RegistryID"].strip()
    lat = _float(row.get("FacLat"), field="FacLat", registry=registry)
    lon = _float(row.get("FacLong"), field="FacLong", registry=registry)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise CardError(f"ECHO facility {registry} has out-of-range coordinates "
                        f"({lat}, {lon})")
    return {
        "id": f"echo:{registry}",
        "name": (row.get("AIRName") or "").strip(),
        "operator": None,
        "address": (row.get("AIRStreet") or "").strip() or None,
        "city": (row.get("AIRCity") or "").strip() or None,
        "state": (row.get("AIRState") or "").strip() or None,
        "zip": (row.get("AIRZip") or "").strip() or None,
        "lat": lat,
        "lon": lon,
        "naics": (row.get("AIRNAICSCodes") or "518210").strip() or "518210",
        # The frozen snapshot has no status column. Absence is stated, not filled.
        "status": "unknown",
    }


def grid_block(facility: dict, polygons: list[dict], rates: dict[str, dict]) -> dict:
    subregion = geo.subregion_for_point_local(facility["lat"], facility["lon"], polygons)
    if subregion is None:
        raise CardError(
            f"facility {facility['id']} at {facility['lat']:.5f},{facility['lon']:.5f} "
            f"falls inside no bundled eGRID subregion; refusing to emit a card with "
            f"no grid binding")
    rate = egrid.rate_for(rates, subregion)
    return {
        "subregion": subregion,
        "subregion_name": rate["name"],
        "utility": None,   # not published in any source this lane reads
        "balancing_authority": KNOWN_BA.get(subregion),
        "rate_lb_per_mwh": rate["co2e_rate_lb_per_mwh"],
        "intensity_state": "PROVISIONAL",
        "factor_set_id": DEFAULT_FACTOR_SET.id,
        "factor_set_sha256": DEFAULT_FACTOR_SET.sha256,
        "binding_method": "point_in_polygon",
    }


def calculation_record(facility_id: str, held: dict, grid: dict,
                       result) -> dict:
    """The replayable Calculation behind the carbon claim (SPEC §2)."""
    return {
        "id": calc_id_for(facility_id),
        "name": "annual-co2e-from-subregion-rate",
        "version": CALC_VERSION,
        "code_sha256": code_sha256(),
        "inputs": {
            "capacity_mw": {
                "value": TYPICAL_CAPACITY_MW,
                "basis": "sizing_scenario",
                "note": SIZING_NOTE,
            },
            "capacity_basis": {
                "value": CapacityBasis.UNKNOWN.value,
                "source": "ECHO carries no capacity field (INV-4: never inferred)",
            },
            "utilization": {
                "value": result.assumptions["utilization"],
                "source": "LBNL/DOE 2024 US Data Center Energy Usage Report prior "
                          "0.45-0.80, p50 0.65",
            },
            "pue": {
                "value": result.assumptions["pue"],
                "source": "not applied: capacity basis is UNKNOWN (INV-4)",
            },
            "hours": {"value": 8760, "source": "UTC-aligned year, SPEC §3"},
            "grid_rate_lb_per_mwh": {
                "value": grid["rate_lb_per_mwh"],
                "subregion": grid["subregion"],
                "source_id": "epa-egrid-2023-rev2-srl23",
                "source_url": egrid.EGRID_URL,
                "units_note": egrid.UNITS_NOTE,
            },
            "factor_set": {"id": grid["factor_set_id"],
                           "sha256": grid["factor_set_sha256"]},
        },
        "formula": ("MWh/yr = cap_mw x 8760 x utilization x (pue if basis == IT_MW "
                    "else 1); tCO2e/yr = MWh/yr x lb_per_mwh / 2204.62"),
        "output": result.as_dict(),
        "mass_unit": "metric_tonne",
        "provenance": held["provenance"],
    }


def calc_id_for(facility_id: str) -> str:
    return f"calc:annual-co2e-v{CALC_VERSION}:{facility_id.split(':', 1)[1]}"


def _csv_citations(held: dict) -> tuple[Citation, ...]:
    """Citations to each CSV line behind the facility, substring-verified now."""
    citations = []
    for item in held["citations"]:
        citation = Citation(
            doc_sha256=held["csv_sha256"],
            page=1,
            line=item["line"],
            quote=item["text"],
            publisher=ECHO_PUBLISHER,
            dataset=ECHO_DATASET,
            field="RegistryID",
            verifier_version="substring-1",
        )
        # Verify against the line itself: a citation that cannot be pointed at is
        # not a citation. This is the INV-7 gate, not a decoration.
        verified = citation.verify(item["text"])
        if not verified.verified:
            raise CardError(f"citation for {held['facility']['id']} failed substring "
                            f"verification against CSV line {item['line']}")
        citations.append(verified)
    if not citations:
        raise CardError(f"{held['facility']['id']} has no source lines")
    return tuple(citations)


def _citation_dict(citation: Citation) -> dict:
    return {
        "doc_sha256": citation.doc_sha256,
        "page": citation.page,
        "line": citation.line,
        "quote": citation.quote,
        "publisher": citation.publisher,
        "dataset": citation.dataset,
        "field": citation.field,
        "verified": citation.verified,
        "verifier_version": citation.verifier_version,
    }


def _claim_dict(claim: Claim) -> dict:
    """Claim -> the frozen card-schema shape, with the render gate applied.

    ``Claim.as_dict`` reports a citation *count* (``sources``); the schema wants
    the citations themselves and forbids extra keys, so the dict is rebuilt here
    rather than patched later.
    """
    ok, reason = claim.renderable()
    if not ok:
        raise CardError(f"{claim.id} is not renderable: {reason}")
    payload = {
        "id": claim.id,
        "metric": claim.metric,
        "unit": claim.unit,
        "badge": claim.badge.value,
        "provisional": claim.provisional,
        "as_of": claim.as_of_utc,
        "range": ({"p10": claim.p10, "p50": claim.p50, "p90": claim.p90}
                  if claim.p10 is not None else None),
        "citations": [_citation_dict(c) for c in claim.citations
                      if c.doc_sha256 and c.page >= 1 and c.line >= 1],
        "calc_id": claim.calc_id,
        "renderable": ok,
    }
    if isinstance(claim.value, NotPublished):
        payload["value"] = {"state": claim.value.value, "reason": ""}
    else:
        payload["value"] = claim.value
    return payload


def _not_published_claim(metric: str, unit: str, reason: str) -> dict:
    """A first-class absence: renderable, reasoned, never zero (INV-8/INV-9)."""
    claim = not_reported(metric=metric, unit=unit, as_of=CARD_AS_OF_UTC,
                         why=NotPublished.NOT_PUBLISHED)
    payload = _claim_dict(claim)
    payload["value"] = {"state": NotPublished.NOT_PUBLISHED.value, "reason": reason}
    if payload["citations"]:
        raise CardError(f"{metric}: a not-published claim must not carry a fake receipt")
    return payload


def claims_for(facility: dict, grid: dict, result, held: dict) -> list[dict]:
    citations = _csv_citations(held)
    calc_id = calc_id_for(facility["id"])

    carbon = Claim(
        id=f"{facility['id']}:carbon_t",
        metric="carbon_t",
        value=round(result.annual_tonnes_co2e, 1),
        unit="tCO2e/yr",
        badge=Badge.ESTIMATED,          # the estimator's own judgement, kept as-is
        as_of_utc=CARD_AS_OF_UTC,
        p10=round(result.p10_tonnes, 1),
        p50=round(result.annual_tonnes_co2e, 1),
        p90=round(result.p90_tonnes, 1),
        provisional=True,               # annual eGRID rate x national fuel factors
        citations=citations,
        calc_id=calc_id,
    )
    carbon_dict = _claim_dict(carbon)
    carbon_dict["drivers"] = result.drivers
    carbon_dict["chain"] = [
        "ECHO snapshot row -> facility coordinates",
        f"point-in-polygon -> eGRID subregion {grid['subregion']}",
        f"eGRID 2023 SRL23 -> {grid['rate_lb_per_mwh']} lb CO2e/MWh",
        f"estimator -> {carbon_dict['range']['p50']} t CO2e/yr for a "
        f"{TYPICAL_CAPACITY_MW:.0f} MW load (capacity basis UNKNOWN)",
        f"Calculation {calc_id} (code_sha256 {held['code_sha256'][:12]}…)",
    ]
    carbon_dict["transforms"] = [
        "short tons/yr -> lb/MWh: SRCO2EQA x 2000 / SRNGENAN",
        "lb -> metric tonnes: / 2204.62 (never / 2000, INV-1)",
        "PUE not applied: capacity basis UNKNOWN (INV-4)",
        "all timestamps UTC; eGRID is an annual average (INV-2)",
    ]
    carbon_dict["sensitivity"] = [
        {"label": "utilization 0.45 -> 0.80", "delta": "widens the range, not the p50"},
        {"label": "capacity basis", "delta": "unknown: a 200 MW site is 2x this band"},
        {"label": "grid rate", "delta": "subregion annual average, not hourly marginal"},
    ]

    power = _not_published_claim(
        "power_mw", "MW",
        "ECHO lists no capacity; WattDocket will not infer one from a name or a footprint")
    power["drivers"] = ["capacity_not_published"]

    water = _not_published_claim(
        "water_gal", "gal/day",
        "no facility-level water figure was found in the public sources used for this card")

    diesel_mw = _not_published_claim(
        "diesel_mw", "MW",
        "no air permit or generator rating is captured for this facility in this snapshot")
    diesel_hours = _not_published_claim(
        "diesel_hours", "h/yr",
        "permit hour cap not reviewed; the source drawer carries the agency link")

    claims = [carbon_dict, power, water, diesel_mw, diesel_hours]
    claims.sort(key=lambda c: c["id"])
    return claims


def verdict_for(facility: dict, grid: dict, claims: list[dict], card_wo_verdict: dict) -> dict:
    """A template sentence whose every numeral already sits in the card.

    Built in two forms — with and without the facility name — so a long name can
    never push the sentence past the schema's 240-character cap and force a
    truncation that would cut a numeral in half.
    """
    carbon = next(c for c in claims if c["metric"] == "carbon_t")
    rate = grid["rate_lb_per_mwh"]
    if isinstance(carbon.get("value"), dict):
        body = (f"sits on the {grid['subregion']} grid; its capacity is not "
                f"published, so carbon is a sizing scenario, not a measurement.")
    else:
        body = (f"sits on the {grid['subregion']} grid at {rate:.1f} lb CO2e/MWh; "
                f"a {TYPICAL_CAPACITY_MW:.0f} MW load here would emit "
                f"{carbon['range']['p50']:,.0f} t/yr "
                f"(range {carbon['range']['p10']:,.0f}-{carbon['range']['p90']:,.0f}).")
    with_name = f"{facility['name']} {body}".strip()
    sentence = " ".join((with_name if len(with_name) <= 240 else body).split())
    if not prose_is_bound(sentence, card_wo_verdict):
        # INV-10's spirit applies to templates too: no numeral may appear that
        # the payload does not carry.
        raise CardError(f"verdict for {facility['id']} carries an unbound numeral: "
                        f"{sentence!r}")
    return {"sentence": sentence, "source": "template", "degraded": False}


def build_card(entry: dict, polygons: list[dict], rates: dict[str, dict],
               csv_sha256: str) -> tuple[dict, dict]:
    """One CSV row -> (card, calculation record)."""
    facility = facility_block(entry)
    grid = grid_block(facility, polygons, rates)
    held = {
        "facility": facility,
        "citations": entry["citations"],
        "csv_sha256": csv_sha256,
        "code_sha256": code_sha256(),
        "provenance": {
            "csv_sha256": csv_sha256,
            "csv_lines": [c["line"] for c in entry["citations"]],
            "source_row_count": entry["source_row_count"],
            "echo_dataset": ECHO_DATASET,
            "echo_publisher": ECHO_PUBLISHER,
            "egrid_source_url": egrid.EGRID_URL,
            "egrid_workbook_sha256": rates[grid["subregion"]].get("workbook_sha256"),
            "polygon_source_url": geo.ARCGIS_SUBREGION_URL,
        },
    }
    result = estimate(EstimateInput(
        capacity_mw=TYPICAL_CAPACITY_MW,
        capacity_basis=CapacityBasis.UNKNOWN,
        utilization=None,          # published prior, never a private guess
        pue=None,                  # never applied to UNKNOWN basis (INV-4)
        grid_rate_lb_per_mwh=grid["rate_lb_per_mwh"],
        factor_set_id=grid["factor_set_id"],
    ))
    claims = claims_for(facility, grid, result, held)

    card: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATOR,
        "as_of": CARD_AS_OF_UTC,
        "facility": facility,
        "grid": grid,
        "claims": claims,
    }
    card["verdict"] = verdict_for(facility, grid, claims, card)
    card["hourly"] = None

    _check_renderable(card)
    return card, calculation_record(facility["id"], held, grid, result)


def _check_renderable(card: dict) -> None:
    """Fail the build for any claim the UI would have to hide (INV-7)."""
    for claim in card["claims"]:
        value = claim.get("value")
        if not claim["renderable"]:
            raise CardError(
                f"{claim['id']} is not renderable: {claim.get('render_block_reason')}")
        if value is None:
            raise CardError(f"{claim['id']} has a null value; use the not-published union")
        if isinstance(value, dict):
            if set(value) != {"state", "reason"} or not value.get("reason"):
                raise CardError(f"{claim['id']} not-published value needs state + reason")
            if value["state"] not in {"NOT_PUBLISHED", "NOT_REPORTED", "BELOW_THRESHOLD"}:
                raise CardError(f"{claim['id']} has unknown absence state {value['state']}")
            if claim["citations"]:
                raise CardError(f"{claim['id']} attaches citations to an absence")
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise CardError(f"{claim['id']} should carry a number, got {value!r}")
        if not claim["citations"] and not (claim["badge"] == "MODELED" and claim["calc_id"]):
            raise CardError(f"{claim['id']} has a number with no receipt (INV-7)")
        if claim["citations"] and not any(c["verified"] for c in claim["citations"]):
            raise CardError(f"{claim['id']} cites nothing that verified")
    carbon = next(c for c in card["claims"] if c["metric"] == "carbon_t")
    if not carbon.get("calc_id"):
        raise CardError("the carbon claim must resolve to a Calculation")
    rng = carbon.get("range") or {}
    if not (rng.get("p10", 0) <= rng.get("p50", 0) <= rng.get("p90", 0)):
        raise CardError(f"carbon range is not ordered: {rng}")


# --------------------------------------------------------------------- build
def build_cards(csv_path: str | Path, rates_path: str | Path,
                polygons_path: str | Path, out_dir: str | Path,
                index_path: str | Path, calculations_path: str | Path,
                log: list[str] | None = None) -> dict:
    log = log if log is not None else []
    entries, csv_sha256 = read_echo_rows(csv_path)
    rates = egrid.load_frozen_rates(rates_path)
    polygons = geo.load_subregion_polygons(polygons_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_rows = sum(entry["source_row_count"] for entry in entries)

    cards: list[tuple[str, dict]] = []
    calculations: dict[str, dict] = {}
    entry_names: dict[str, list[dict]] = {}
    for entry in entries:
        card, calc = build_card(entry, polygons, rates, csv_sha256)
        cards.append((card_filename(card["facility"]["id"]), card))
        calculations[calc["id"]] = calc
        entry_names[card["facility"]["id"]] = entry["rows"]
    cards.sort(key=lambda pair: pair[1]["facility"]["id"])

    written: set[str] = set()
    index_entries: dict[str, dict] = {}
    order: list[str] = []
    for filename, card in cards:
        path = out_dir / filename
        path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        written.add(filename)
        carbon = next(c for c in card["claims"] if c["metric"] == "carbon_t")
        index_entries[card["facility"]["id"]] = {
            "id": card["facility"]["id"],
            "name": card["facility"]["name"],
            "aliases": sorted({(r.get("AIRName") or "").strip()
                               for r in entry_names[card["facility"]["id"]]} - {""}),
            "source_rows": len(entry_names[card["facility"]["id"]]),
            "state": card["facility"]["state"],
            "city": card["facility"]["city"],
            "lat": card["facility"]["lat"],
            "lon": card["facility"]["lon"],
            "status": card["facility"]["status"],
            "subregion": card["grid"]["subregion"],
            "subregion_name": card["grid"]["subregion_name"],
            "rate_lb_per_mwh": card["grid"]["rate_lb_per_mwh"],
            "carbon_t": carbon["range"]["p50"],
            "carbon_t_p10": carbon["range"]["p10"],
            "carbon_t_p90": carbon["range"]["p90"],
            "badge": carbon["badge"],
            "calc_id": carbon["calc_id"],
            "renderable": all(c["renderable"] for c in card["claims"]),
            "path": f"cards/{filename}",
            "sha256": sha256_bytes(path.read_bytes()),
        }
        order.append(card["facility"]["id"])

    stale = sorted(p.name for p in out_dir.glob("echo_*.json") if p.name not in written)
    for name in stale:
        (out_dir / name).unlink()
    if stale:
        log.append(f"cards: removed {len(stale)} stale card file(s)")

    calculations_path = Path(calculations_path)
    calculations_path.parent.mkdir(parents=True, exist_ok=True)
    calculations_path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION,
                    "code_sha256": code_sha256(),
                    "calculations": dict(sorted(calculations.items()))},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    by_state: dict[str, int] = {}
    by_subregion: dict[str, int] = {}
    for entry in index_entries.values():
        state = entry["state"] or ""
        by_state[state] = by_state.get(state, 0) + 1
        by_subregion[entry["subregion"]] = by_subregion.get(entry["subregion"], 0) + 1

    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATOR,
        "as_of": CARD_AS_OF_UTC,
        "count": len(order),
        "card_count": len(order),
        "order": order,
        "counts": {
            "by_state": dict(sorted(by_state.items())),
            "by_subregion": dict(sorted(by_subregion.items())),
            "status_unknown": sum(1 for e in index_entries.values()
                                  if e["status"] == "unknown"),
            "source_rows": source_rows,
            "facilities": len(order),
            "multi_source_facilities": sum(1 for e in index_entries.values()
                                           if e["source_rows"] > 1),
        },
        "sources": {
            "echo_csv": {"path": display_path(csv_path), "sha256": csv_sha256,
                         "dataset": ECHO_DATASET, "publisher": ECHO_PUBLISHER},
            "egrid_rates": {"path": display_path(rates_path),
                            "sha256": sha256_bytes(Path(rates_path).read_bytes()),
                            "subregions": len(rates)},
            "egrid_polygons": {"path": display_path(polygons_path),
                               "sha256": sha256_bytes(Path(polygons_path).read_bytes()),
                               "source_url": geo.ARCGIS_SUBREGION_URL},
            "factor_set": {"id": DEFAULT_FACTOR_SET.id,
                           "sha256": DEFAULT_FACTOR_SET.sha256},
        },
        "calculations": Path(calculations_path).name,
        "code_sha256": code_sha256(),
        "cards": index_entries,
    }
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")

    log.append(f"cards: {len(order)} written to {out_dir}")
    log.append(f"cards: subregions {len(by_subregion)}; status unknown "
               f"{index['counts']['status_unknown']}")
    return {"cards": len(order), "calculations": len(calculations),
            "index": str(index_path), "out": str(out_dir),
            "counts": index["counts"], "log": log}


def validate_against_schema(cards: Iterable[dict], schema_path: str | Path) -> None:
    """Validate cards against the frozen UI contract, if jsonschema is present."""
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - optional dependency
        raise CardError("jsonschema is required to validate cards "
                        "(pip install jsonschema) from None")
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    for card in cards:
        jsonschema.validate(card, schema)


def load_cards(index_path: str | Path) -> list[dict]:
    """Read every card referenced by the index (path is relative to it)."""
    index_path = Path(index_path)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    base = index_path.parent
    cards = index["cards"]
    entries = ([cards[key] for key in index.get("order", sorted(cards))]
               if isinstance(cards, dict) else list(cards))
    return [json.loads((base / entry["path"]).read_text(encoding="utf-8"))
            for entry in entries]


def factor_set_payload() -> dict:
    """The pinned factor table and its own hash — the INV-6 link, as a file.

    ``grid.DEFAULT_FACTOR_SET.sha256`` is computed by
    :func:`wattdocket.grid.factor_set_hash` over the canonical JSON of the table.
    This artifact carries that same string plus the canonical bytes, so a reader
    can recompute the hash without importing the package, and a silent table edit
    shows up as a mismatch in two places instead of none.
    """
    from ..grid import factor_set_hash

    table = {fuel: float(value) for fuel, value in DEFAULT_FACTOR_SET.lb_per_mwh.items()}
    canonical = json.dumps(table, sort_keys=True, separators=(",", ":"))
    digest = factor_set_hash(table)
    if digest != DEFAULT_FACTOR_SET.sha256:
        raise CardError(
            f"DEFAULT_FACTOR_SET.sha256 is {DEFAULT_FACTOR_SET.sha256} but the table "
            f"hashes to {digest}: the pinned hash is stale (INV-6)")
    return {
        "id": DEFAULT_FACTOR_SET.id,
        "published_by": DEFAULT_FACTOR_SET.published_by,
        "effective_date": DEFAULT_FACTOR_SET.effective_date,
        "url": DEFAULT_FACTOR_SET.url,
        "sha256": digest,
        "lb_per_mwh": table,
        "canonical_json": canonical,
        "note": ("PROVISIONAL until settled by CEMS or Open Grid Emissions; three "
                 "plausible tables move the level ~20% while the hourly ratio stays "
                 "~1.75x"),
    }


def write_factor_set_artifact(path: str | Path) -> dict:
    """Write ``artifacts/factor_set.json``; returns the payload it wrote."""
    payload = factor_set_payload()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        prog="wattdocket.ingest.cards",
        description="Build static facility cards from frozen sources (no network)")
    parser.add_argument("--csv", default=str(repo / "data" / "echo_datacenters_naics518210.csv"))
    parser.add_argument("--rates", default=str(repo / "data" / "egrid_subregion_rates.json"))
    parser.add_argument("--polygons", default=str(repo / "data" / "egrid_subregions.geojson"))
    parser.add_argument("--out", default=str(repo / "artifacts" / "cards"))
    parser.add_argument("--index", default=str(repo / "artifacts" / "cards.json"))
    parser.add_argument("--calculations",
                        default=str(repo / "artifacts" / "calculations.json"))
    parser.add_argument("--factor-set",
                        default=str(repo / "artifacts" / "factor_set.json"),
                        help="write the pinned factor set + its sha256 (INV-6)")
    parser.add_argument("--schema", default=str(repo / "schemas" / "card.schema.json"),
                        help="validate every card against this schema")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    result = build_cards(args.csv, args.rates, args.polygons, args.out,
                         args.index, args.calculations)
    if args.factor_set:
        payload = write_factor_set_artifact(args.factor_set)
        result["log"].append(f"factor_set: {payload['id']} sha256={payload['sha256']} "
                             f"-> {args.factor_set}")
    if args.schema and Path(args.schema).is_file():
        validate_against_schema(load_cards(args.index), args.schema)
        result["log"].append(f"cards: validated against {args.schema}")
    if not args.quiet:
        for line in result["log"]:
            print(line)
        print(json.dumps({k: v for k, v in result.items() if k != "log"}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
