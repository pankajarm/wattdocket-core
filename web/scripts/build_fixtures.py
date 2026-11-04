#!/usr/bin/env python3
"""Build the static fixtures that let the UI run with no API.

Reads the frozen ECHO snapshot in ../data/ and writes:

  public/data/facilities.json                 all 479 ECHO rows, indexed
  public/data/fixture-facility-<slug>.json    public card payload (3 heroes)
  public/data/fixture-drawer-<slug>-<claim>.json
  public/data/fixture-operator-<slug>.json

FIXTURES ARE SYNTHETIC. Capacities, utilization, diesel permits and the permit
quotes below are realistic but invented for a demo; they are not the output of
the production estimator and must not be presented as evidence. The three
facilities themselves are real ECHO rows (name, address, coordinates, registry
id come from the CSV).

Arithmetic that the UI will display is computed here, not typed by hand, and
asserted: metric tonnes from lb (INV-1), p10 < p50 < p90, water never a scalar
(INV-9), and each citation's highlighted substring really occurs in its quote.

Usage:  python3 scripts/build_fixtures.py
"""

from __future__ import annotations

import csv
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "data" / "echo_datacenters_naics518210.csv"
OUT = pathlib.Path(__file__).resolve().parents[1] / "public" / "data"

LB_PER_TONNE = 2204.62


def tonnes(mwh: float, lb_per_mwh: float) -> int:
    """INV-1: metric tonnes, always ÷2204.62."""
    return round(mwh * lb_per_mwh / LB_PER_TONNE)


def mwh(cap_mw: float, util: float, pue: float = 1.0) -> int:
    return round(cap_mw * 8760 * util * pue)


DIRECTIONS = {"NE", "NW", "SE", "SW", "N", "S", "E", "W", "PO", "BOX", "US", "RT"}


def pretty_street(text: str) -> str:
    """ECHO ships screaming-caps addresses; keep the cardinal directions loud."""
    out = []
    for word in text.strip().split(" "):
        bare = word.strip(".,#").upper()
        if bare in DIRECTIONS:
            out.append(word.upper())
        elif any(ch.isalpha() for ch in word):
            out.append(word[:1].upper() + word[1:].lower() if word.isupper() else word)
        else:
            out.append(word)
    return " ".join(out)


def slug(facility_id: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in facility_id.lower()).strip("-").replace("--", "-")


def read_rows() -> list[dict[str, str]]:
    with CSV_PATH.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------- index

def build_index(rows: list[dict[str, str]], heroes: set[str]) -> list[dict]:
    seen: dict[str, int] = {}
    out: list[dict] = []
    for r in rows:
        reg = r["RegistryID"].strip()
        seen[reg] = seen.get(reg, 0) + 1
        fid = f"echo:{reg}" if seen[reg] == 1 else f"echo:{reg}-{seen[reg]}"
        entry = {
            "id": fid,
            "name": r["AIRName"].strip(),
            "address_line": pretty_street(r["AIRStreet"]),
            "city": r["AIRCity"].strip().title(),
            "state": r["AIRState"].strip(),
            "zip": r["AIRZip"].strip(),
            "lat": float(r["FacLat"]),
            "lon": float(r["FacLong"]),
            "registry_id": reg,
        }
        if seen[reg] > 1:
            entry["duplicate_of"] = f"echo:{reg}"
        if fid in heroes:
            entry["hero"] = True
        out.append(entry)
    return out


# ---------------------------------------------------------------- hourly grid

def readings(day: str, values: list[float], ba: str = "PJM") -> dict:
    assert len(values) == 24, len(values)
    series = []
    for hour, v in enumerate(values):
        series.append(
            {
                "ba_code": ba,
                "interval_start_utc": f"{day}T{hour:02d}:00:00Z",
                "interval_minutes": 60,
                "intensity_lb_co2e_per_mwh": round(v, 1),
                "intensity_state": "PROVISIONAL",
                "factor_set_id": "fs_egrid2023_srvc_v1",
            }
        )
    return {
        "ba_code": ba,
        "day": day,
        "intensity_state": "PROVISIONAL",
        "factor_set_id": "fs_egrid2023_srvc_v1",
        "factor_set_label": "EPA eGRID2023 · SRVC · rev2",
        "interval_count": 288,
        "interval_minutes": 5,
        "readings": series,
    }


# -------------------------------------------------------------- citation text

def citation(cid, claim_id, quote, matched, publisher, doc_id, page, line, url=None, verified=True):
    assert matched in quote, f"matched text not in quote: {cid}"
    return {
        "id": cid,
        "claim_id": claim_id,
        "document_sha256": "4f9c1d7b8a2e6f3c9d0b5a4e7c1f2d8b6a3e9c0f5d2b7a4e1c8f6b3d9a0e7c2f"
        if cid.endswith("carbon")
        else "9b2e4c7a1f6d8e3b5c0a9d2f7e4b1c6a8d3f0e5b2c9a7d4f1e6b8c3a0d5f2e9b",
        "publisher": publisher,
        "document_id": doc_id,
        "page": page,
        "line": line,
        "quote": quote,
        "matched_text": matched,
        "verified": verified,
        "verified_at_utc": "2026-10-27T06:12:00Z" if verified else "2026-10-27T06:12:00Z",
        "verifier_version": "substring-verify@0.4.1",
        "url": url,
    }


# --------------------------------------------------------------------- claims

def numeric_claim(
    fid, cid, metric, label, value, unit, p10, p50, p90, badge,
    sources, as_of, drivers, provisional=False, unit_note=None,
):
    assert p10 < p50 < p90, (cid, p10, p50, p90)
    c = {
        "id": cid,
        "facility_id": fid,
        "metric": metric,
        "label": label,
        "value": {"kind": "NUMERIC", "value": value, "unit": unit},
        "p10": p10,
        "p50": p50,
        "p90": p90,
        "badge": badge,
        "provisional": provisional,
        "as_of_utc": as_of,
        "source_count": len(sources),
        "citations": sources,
        "drivers": drivers,
        "calc_id": f"calc:{metric}@0.3",
        "renderable": True,
    }
    if unit_note:
        c["unit_note"] = unit_note
    return c


def unpublished_claim(fid, cid, metric, label, reason, method_note, sources, as_of, badge="ESTIMATED"):
    return {
        "id": cid,
        "facility_id": fid,
        "metric": metric,
        "label": label,
        "value": {"kind": "NOT_PUBLISHED", "reason": reason, "method_note": method_note},
        "p10": None,
        "p50": None,
        "p90": None,
        "badge": badge,
        "provisional": False,
        "as_of_utc": as_of,
        "source_count": len(sources),
        "citations": sources,
        "drivers": ["no statutory reporting path"],
        "calc_id": None,
        "renderable": True,
    }


def band_claim(fid, cid, metric, label, low, high, unit, method_note, sources, as_of, badge="ESTIMATED"):
    assert low < high
    return {
        "id": cid,
        "facility_id": fid,
        "metric": metric,
        "label": label,
        "value": {"kind": "BAND", "unit": unit, "low": low, "high": high, "method_note": method_note},
        "p10": None,
        "p50": None,
        "p90": None,
        "badge": badge,
        "provisional": False,
        "as_of_utc": as_of,
        "source_count": len(sources),
        "citations": sources,
        "drivers": ["cooling_type", "capacity_basis"],
        "calc_id": f"calc:{metric}@0.1",
        "renderable": True,
    }


# ------------------------------------------------------------------ facilities

AS_OF = "2026-10-26T00:00:00Z"
CLAIM_ID = {"power": "clm-power", "carbon": "clm-carbon", "water": "clm-water", "diesel": "clm-diesel"}

HEROES: list[dict] = [
    {
        "registry": "110044950274",
        "fid": "echo:110044950274",
        "county": "Loudoun County",
        "operator": "21110 Ridgetop Circle LLC",
        "capacity_mw": 84.0,
        "basis": "GRID_MW",
        "util": 0.72,
        "util_low": 0.45,
        "util_high": 0.80,
        "pue": 1.0,
        "cap_low": 52.0,
        "cap_high": 121.0,
        "rate": 593.42,
        "diesel_mw": 6.2,
        "diesel_engines": 8,
        "diesel_cap_hours": 100,
        "verdict": "A large load — roughly **a third of a mid-size city's homes** — on a grid slightly dirtier than the US average. Most of its impact is **when** it draws power, not what it draws.",
        "chain_suffix": "+ capacity band (84 → 121 MW) → p90 228,258",
        "hourly": [618, 604, 597, 590, 585, 601, 634, 682, 731, 622, 592, 566,
                   562, 571, 606, 668, 742, 861, 1006, 1026, 998, 941, 812, 703],
        "water": ("NOT_PUBLISHED",
                  "no US state requires site-level operational water reporting",
                  "regional prior only · WRI Aqueduct: Loudoun County medium-high stress (2.9/5)"),
    },
    {
        "registry": "110070890991",
        "fid": "echo:110070890991",
        "county": "Prince William County",
        "operator": "Amazon Data Services",
        "capacity_mw": 204.0,
        "basis": "GRID_MW",
        "util": 0.68,
        "util_low": 0.45,
        "util_high": 0.80,
        "pue": 1.0,
        "cap_low": 148.0,
        "cap_high": 268.0,
        "rate": 593.42,
        "diesel_mw": 12.4,
        "diesel_engines": 16,
        "diesel_cap_hours": 100,
        "verdict": "One of the largest single loads in Prince William County. On this grid its annual carbon is **fixed by the subregion factor**, not by how efficiently the building runs — the band is mostly **how big it really is**.",
        "chain_suffix": "+ capacity band (204 → 268 MW) → p90 505,610",
        "hourly": [641, 628, 617, 609, 604, 618, 649, 701, 748, 639, 607, 581,
                   578, 588, 623, 686, 760, 892, 1021, 1051, 1022, 962, 833, 721],
        "water": ("NOT_PUBLISHED",
                  "the serving utility publishes system totals, not per-site withdrawals",
                  "regional prior only · Prince William Water system average; no facility meter in any filing"),
    },
    {
        "registry": "110001914821",
        "fid": "echo:110001914821",
        "county": "Loudoun County",
        "operator": "22810-22860 International Drive LLC",
        "capacity_mw": 36.0,
        "basis": "IT_MW",
        "util": 0.70,
        "util_low": 0.55,
        "util_high": 0.85,
        "pue": 1.18,
        "cap_low": 24.0,
        "cap_high": 51.0,
        "rate": 593.42,
        "diesel_mw": 3.1,
        "diesel_engines": 4,
        "diesel_cap_hours": 100,
        "verdict": "A mid-size colocation hall on the same dirty-evening grid. Its carbon receipt is **blocked in this build** — the permit quote failed verification — so the card says so instead of showing a number.",
        "chain_suffix": "+ capacity band (36 → 51 MW) → p90 148,116",
        "hourly": [607, 598, 590, 584, 579, 593, 626, 671, 718, 611, 585, 561,
                   559, 566, 599, 659, 731, 848, 989, 1009, 982, 926, 799, 692],
        "water": ("BAND",
                  "0.9–1.4",
                  "cooling towers at 36 MW IT × Loudoun climate · no published site meter"),
    },
]


def build_facility(hero: dict, csv_row: dict[str, str]) -> dict:
    fid = hero["fid"]
    cap = hero["capacity_mw"]
    basis = hero["basis"]
    pue = hero["pue"]
    util = hero["util"]

    mwh_central = mwh(cap, util, pue)
    base_t = tonnes(mwh_central, hero["rate"])
    # The power-draw band already carries utilization + basis uncertainty, so the
    # carbon band is the same capacity band run through the same factor. Stacking
    # util_min AND cap_min on the low end would double-count one driver.
    pue_low = pue
    pue_high = pue
    t_low = tonnes(mwh(hero["cap_low"], util, pue_low), hero["rate"])
    t_high = tonnes(mwh(hero["cap_high"], util, pue_high), hero["rate"])
    p10, p50, p90 = sorted([t_low, base_t, t_high])

    # --- citations -----------------------------------------------------------
    carbon_cite = citation(
        f"cit-{hero['registry']}-carbon", CLAIM_ID["carbon"],
        "For the SERC Virginia/Carolina subregion the annual CO\u2082e total-output "
        "emission rate is 593.4 lb/MWh, and the total-output emission rate for all "
        "nonshared generation is 601.2 lb/MWh.",
        "593.4 lb/MWh",
        "US EPA", "EPA eGRID2023 rev2 · SRL23", 14, "3",
        "https://www.epa.gov/egrid/download-data",
    )
    diesel_quote = (
        f"\u2026the facility is authorized to operate {hero['diesel_engines']} diesel-fired "
        f"emergency generator engines with a combined rated capacity of {hero['diesel_mw']} MW, "
        f"limited to {hero['diesel_cap_hours']} hours per year per engine for maintenance and testing\u2026"
    )
    diesel_cite = citation(
        f"cit-{hero['registry']}-diesel", CLAIM_ID["diesel"],
        diesel_quote,
        f"{hero['diesel_engines']} diesel-fired emergency generator engines",
        "Virginia DEQ", "Air permit · registration 70512", 4, "12",
        "https://www.deq.virginia.gov/permits-regulations/air",
    )
    water_cite = citation(
        f"cit-{hero['registry']}-water", CLAIM_ID["water"],
        "No facility-specific withdrawal volume is reported for this site in the "
        "state water withdrawal database; only the permitted maximum daily withdrawal "
        "for the parcel is recorded.",
        "No facility-specific withdrawal volume",
        "Virginia DEQ", "Water withdrawal database · parcel lookup", None, None,
        "https://www.deq.virginia.gov/water",
    )
    power_cite = citation(
        f"cit-{hero['registry']}-power", CLAIM_ID["power"],
        f"The premises are served by a {int(cap * 1.0)} MVA dedicated substation feed with "
        "two independent service entrances and on-site transformation.",
        f"{int(cap)} MVA dedicated substation",
        "Dominion Energy", "Service territory interconnection queue", None, None,
        "https://www.dominionenergy.com/",
    )

    power = numeric_claim(
        fid, CLAIM_ID["power"], "power_mw", "Power draw",
        cap, "MW",
        round(hero["cap_low"], 1), cap, round(hero["cap_high"], 1),
        "MODELED", [power_cite, carbon_cite, diesel_cite], AS_OF,
        ["capacity_basis", "utilization", "pue"] if basis == "IT_MW" else ["capacity_basis", "utilization"],
    )

    carbon = numeric_claim(
        fid, CLAIM_ID["carbon"], "carbon_t", "Carbon",
        p50, "t CO\u2082e/yr", p10, p50, p90,
        "MODELED", [carbon_cite, power_cite, diesel_cite, water_cite], AS_OF,
        ["capacity_basis", "utilization", "pue"] if basis == "IT_MW" else ["capacity_basis", "utilization"],
        provisional=True,
    )

    water_kind = hero["water"][0]
    if water_kind == "NOT_PUBLISHED":
        water = unpublished_claim(
            fid, CLAIM_ID["water"], "water_gal", "Water",
            hero["water"][1], hero["water"][2], [water_cite], AS_OF,
        )
    else:
        water = band_claim(
            fid, CLAIM_ID["water"], "water_gal", "Water",
            0.9, 1.4, "Mgal/d", hero["water"][2], [water_cite], AS_OF,
        )

    diesel = numeric_claim(
        fid, CLAIM_ID["diesel"], "diesel_mw", "Backup diesel",
        hero["diesel_mw"], "MW",
        round(hero["diesel_mw"] * 0.78, 1), hero["diesel_mw"], round(hero["diesel_mw"] * 1.19, 1),
        "REPORTED", [diesel_cite, power_cite], AS_OF, ["engine_count"],
        unit_note=f"MW · {hero['diesel_cap_hours']} h/yr cap",
    )

    facility = {
        "id": fid,
        "name": csv_row["AIRName"].strip(),
        "operator": hero["operator"],
        "naics": "518210",
        "lat": float(csv_row["FacLat"]),
        "lon": float(csv_row["FacLong"]),
        "address_line": pretty_street(csv_row["AIRStreet"]),
        "city": csv_row["AIRCity"].strip().title(),
        "state": csv_row["AIRState"].strip(),
        "zip": csv_row["AIRZip"].strip(),
        "county": hero["county"],
        "status": "operating",
        "status_source_id": "src-echo-2026-10-27",
        "capacity_mw": cap,
        "capacity_basis": basis,
        "capacity_source_id": f"cit-{hero['registry']}-power",
    }

    grid = {
        "facility_id": fid,
        "utility": "Dominion Energy Virginia",
        "balancing_authority": "PJM",
        "egrid_subregion": "SRVC",
        "subregion_label": "SERC Virginia/Carolina",
        "method": "point_in_polygon",
        "source_id": "src-egrid2023-polygons",
        "rate_lb_per_mwh": hero["rate"],
        "intensity_state": "PROVISIONAL",
        "factor_set_label": "EPA eGRID2023 · SRLC · rev2",
    }

    payload = {
        "as_of": AS_OF,
        "calc_version": "estimator@0.3.1",
        "receipts": [c["id"] for c in (power_cite, carbon_cite, diesel_cite, water_cite)],
        "facility": facility,
        "grid": grid,
        "claims": [power, carbon, water, diesel],
        "hourly": readings("2026-10-26", hero["hourly"]),
        "verdict": {
            "template": hero["verdict"],
            "bound_claim_ids": [CLAIM_ID["power"], CLAIM_ID["carbon"], CLAIM_ID["water"]],
        },
        "_arith": {"mwh": mwh_central, "base_t": base_t},  # stripped before write
    }
    return payload


# --------------------------------------------------------------------- drawer

def common_drawer(claim: dict, citation_primary: dict, steps: list[dict], transforms, sensitivity, extras=None):
    return {
        "claim_id": claim["id"],
        "metric": claim["metric"],
        "label": claim["label"],
        "headline": "",
        "unit": claim["value"].get("unit", ""),
        "badge": claim["badge"],
        "provisional": claim["provisional"],
        "p10": claim["p10"],
        "p50": claim["p50"],
        "p90": claim["p90"],
        "as_of_utc": claim["as_of_utc"],
        "receipt_count": claim["source_count"],
        "citation": citation_primary,
        "extra_citations": extras or [],
        "chain": {
            "calc_id": claim["calc_id"] or "n/a",
            "name": f"loadproof.{claim['metric'].replace('_mw', '').replace('_t', '')}",
            "version": "0.3.1",
            "code_sha256": "c1a4f0e7b3d95a2c8f61e0b47d3a9c5e2f8b6d1a0c4e7f3b9d2a5c8e1f4b7d0a",
            "steps": steps,
        },
        "transforms": transforms,
        "sensitivity": sensitivity,
        "drivers": claim["drivers"],
    }


def build_drawers(hero: dict, payload: dict) -> list[tuple[str, dict]]:
    fid = hero["fid"]
    claims = {c["metric"]: c for c in payload["claims"]}
    carbon, power, diesel, water = claims["carbon_t"], claims["power_mw"], claims["diesel_mw"], claims["water_gal"]
    a = payload["_arith"]
    cap, util, pue, basis = hero["capacity_mw"], hero["util"], hero["pue"], hero["basis"]
    out = []

    # carbon ---------------------------------------------------------------
    pue_term = f" × {pue} PUE" if basis == "IT_MW" else ""
    steps = [
        {"line": "loadproof.carbon@p0.3 · inputs sealed", "kind": "call"},
        {
            "line": f"{cap:g} MW × 8760 h × {util:.2f} util{pue_term} → {a['mwh']:,} MWh",
            "kind": "compute",
        },
        {"line": f"× {hero['rate']:.2f} lb/MWh ÷ 2204.62 → {a['base_t']:,} t", "kind": "compute"},
        {
            "line": f"capacity band {hero['cap_low']:g}–{hero['cap_high']:g} MW → p10 {carbon['p10']:,}",
            "kind": "band",
        },
        {"line": f"capacity upside {hero['cap_high']:g} MW → p90 {carbon['p90']:,}", "kind": "band"},
        {"line": "assert mass ÷2204.62 (metric t) · assert p10 < p50 < p90 · ok", "kind": "guard"},
    ]
    if hero["registry"] == "110001914821":
        steps.insert(4, {"line": "receipt.verify() → FAILED · p.4 l.12 quote not found in archived PDF", "kind": "guard"})
    carbon_drawer = common_drawer(
        carbon, carbon["citations"][0], steps,
        [
            {"label": "lb → metric tonnes", "value": "÷ 2204.62"},
            {"label": "PUE applied?", "value": "yes — IT basis × 1.18" if basis == "IT_MW" else "no — MW is facility basis"},
            {"label": "battery storage", "value": "excluded (charging ≠ negative gen)"},
            {"label": "time basis", "value": "UTC, DST-safe"},
        ],
        [
            {"label": "marginal instead of average", "delta": "+38%", "tone": "up"},
            {"label": f"if utilization {hero['util_low']:.2f}", "delta": f"{round((hero['util_low'] / util - 1) * 100)}%", "tone": "down"},
            {"label": "if PUE 1.25 (IT basis)", "delta": "+25%", "tone": "up"},
        ],
        extras=carbon["citations"][1:],
    )
    carbon_drawer["headline"] = f"{carbon['p50']:,}"
    out.append((carbon["id"], carbon_drawer))

    # power ----------------------------------------------------------------
    power_drawer = common_drawer(
        power, power["citations"][0],
        [
            {"line": "loadproof.capacity@p0.3 · inputs sealed", "kind": "call"},
            {"line": f"substation feed 84 MVA × 0.95 pf → {power['p50']:.0f} MW facility", "kind": "compute"},
            {"line": f"± capacity band {power['p10']:g}–{power['p90']:g} MW from permit floor and campus ceiling", "kind": "band"},
            {"line": "assert basis = GRID_MW · PUE not applied · ok", "kind": "guard"},
        ],
        [
            {"label": "MVA → MW", "value": "× 0.95 power factor"},
            {"label": "basis", "value": "GRID_MW (facility meter)"},
            {"label": "IT vs facility", "value": "IT load ≈ 71 MW · PUE 1.18"},
            {"label": "peak vs average", "value": "84 MW peak, 0.72 annual load factor"},
        ],
        [
            {"label": "if 100% IT load growth", "delta": "+44%", "tone": "up"},
            {"label": "if pf 0.90", "delta": "−5%", "tone": "down"},
            {"label": "if campus adds a hall", "delta": "+25%", "tone": "up"},
        ],
        extras=power["citations"][1:],
    )
    power_drawer["headline"] = f"{power['p50']:g}"
    out.append((power["id"], power_drawer))

    # diesel ---------------------------------------------------------------
    diesel_drawer = common_drawer(
        diesel, diesel["citations"][0],
        [
            {"line": "loadproof.permit@p0.2 · inputs sealed", "kind": "call"},
            {"line": f"{hero['diesel_engines']} engines × {hero['diesel_mw'] / hero['diesel_engines']:.2f} MW nameplate → {hero['diesel_mw']} MW", "kind": "compute"},
            {"line": f"hour cap {hero['diesel_cap_hours']} h/yr ⇒ max {round(hero['diesel_mw'] * hero['diesel_cap_hours']):,} MWh/yr", "kind": "compute"},
            {"line": "permit is a ceiling, not an activity record · ok", "kind": "guard"},
        ],
        [
            {"label": "hours basis", "value": "per engine, per year"},
            {"label": "fuel", "value": "ULSD, 2 ppm sulfur"},
            {"label": "Scope-1 CO₂e at cap", "value": "≈ 1,284 t/yr"},
            {"label": "GHGRP reporting", "value": "below 25 kt threshold — not reported"},
        ],
        [
            {"label": "if all 100 h used", "delta": "+28%", "tone": "up"},
            {"label": "if 2 engines retired", "delta": "−25%", "tone": "down"},
            {"label": "if tested monthly", "delta": "−62%", "tone": "down"},
        ],
        extras=diesel["citations"][1:],
    )
    diesel_drawer["headline"] = f"{diesel['p50']:g}"
    out.append((diesel["id"], diesel_drawer))

    # water ----------------------------------------------------------------
    if water["value"]["kind"] == "NOT_PUBLISHED":
        water_steps = [
            {"line": "loadproof.water@p0.1 · no numeric path", "kind": "call"},
            {"line": "search(state withdrawal DB) → 0 facility-level rows", "kind": "compute"},
            {"line": "search(utility CCR, ECHO DFR) → system totals only", "kind": "compute"},
            {"line": "INV-9: refuse scalar · return NOT_PUBLISHED with regional prior", "kind": "guard"},
        ]
        water_head = "Not published"
    else:
        water_steps = [
            {"line": "loadproof.water@p0.1 · IT_MW basis", "kind": "call"},
            {"line": "cooling = evaporative towers · 36 MW IT × 1.18 PUE", "kind": "compute"},
            {"line": "specific withdrawal 0.55–0.85 L/kWh(th) · Loudoun climate bin", "kind": "band"},
            {"line": "INV-9: emit band 0.9–1.4 Mgal/d, never a scalar · ok", "kind": "guard"},
        ]
        water_head = "0.9–1.4"
    water_drawer = common_drawer(
        water, water["citations"][0], water_steps,
        [
            {"label": "unit", "value": "Mgal/day, not annual"},
            {"label": "scalar water number", "value": "never rendered (INV-9)"},
            {"label": "cooling type", "value": "evaporative, closed loop"},
            {"label": "consumption vs withdrawal", "value": "≈ 75% consumed"},
        ],
        [
            {"label": "if air-cooled retrofit", "delta": "−92%", "tone": "down"},
            {"label": "if WUE 1.8 L/kWh", "delta": "+31%", "tone": "up"},
            {"label": "if drought curtailment", "delta": "−18%", "tone": "down"},
        ],
    )
    water_drawer["headline"] = water_head
    out.append((water["id"], water_drawer))
    return out


# ------------------------------------------------------------------- operator

def heatmap() -> list[dict]:
    cells = []
    for month in range(1, 13):
        winter = month in (11, 12, 1, 2, 3)
        summer = month in (5, 6, 7, 8, 9)
        for hour in range(24):
            evening = 18 <= hour <= 23
            if winter and evening:
                v = None  # unmatched — red
            elif winter and 16 <= hour <= 17:
                v = 0.45
            elif summer and evening:
                v = 0.72
            elif summer:
                v = 1.0
            elif evening:
                v = 0.58
            else:
                v = 0.92
            cells.append({"month": month, "hour": hour, "matched": v})
    return cells


def build_operator(hero: dict, facility: dict, carbon_p50: int, matched_mwh_pct: float) -> dict:
    hourly = round(carbon_p50 * (1 - matched_mwh_pct / 100))
    loc = carbon_p50
    return {
        "as_of": AS_OF,
        "calc_version": "scenario@0.2.0",
        "facility_id": hero["fid"],
        "facility_label": f"{facility['address_line']}, {facility['city']}, {facility['state']}",
        "synthetic_meter_data": True,
        "methods": [
            {"key": "location", "label": "Location-based", "tonnes": loc,
             "note": "eGRID SRVC annual factor on metered MWh", "tone": "red"},
            {"key": "market", "label": "Market-based (RECs)", "tonnes": 0,
             "note": "annual unbundled RECs, no hourly claim", "tone": "teal"},
            {"key": "hourly", "label": "24/7 hourly matched", "tonnes": hourly,
             "note": "hour-by-hour CFE match, residual = unmatched hours", "tone": "amber"},
        ],
        "matching_note": "You can claim 0 t with annual RECs and still emit the full location figure at the stack. Hourly matching is the honest middle: **63.7% of hours matched** but only **36.0% of MWh**, gap concentrated 18:00–23:00 Nov–Feb.",
        "matched_pct": matched_mwh_pct,
        "compliance": [
            {"key": "generator_hours", "label": "Generator hours YTD", "value": "78", "unit": "/ 100 h",
             "sub": "federal maintenance allowance · state permit caps at 84 h", "pct": 93, "mark_pct": 84, "tone": "red"},
            {"key": "water_limit", "label": "Water vs permit ceiling", "value": "84", "unit": "%",
             "sub": "modelled band midpoint · permit ceiling 2026-07", "pct": 84, "mark_pct": None, "tone": "amber"},
        ],
        "peak": {"key": "peak", "label": "Peak demand & basis", "value": f"{hero['capacity_mw']:g}",
                 "unit": f" MW facility · {round(hero['capacity_mw'] / 1.18)} MW IT",
                 "sub": "metered 15-min intervals · 8,760 rows · PUE measured 1.18 (not assumed)",
                 "pct": round(100 / 1.18), "mark_pct": None, "tone": "blue"},
        "levers": [
            {"rank": 1, "title": "Shift 15% of batch load off 18:00–23:00",
             "detail": "uses marginal factors, not averages",
             "tonnes": -round(carbon_p50 * 0.099), "cost": "$0 capex", "tone": "teal"},
            {"rank": 2, "title": "60 MW wind PPA, same balancing authority",
             "detail": "adds 412 matched hours",
             "tonnes": -round(carbon_p50 * 0.224), "cost": "$41/t", "tone": "teal"},
            {"rank": 3, "title": "Hybrid cooling retrofit (evap → adiabatic)",
             "detail": "cuts summer water 38%, adds 2.1% power",
             "tonnes": -round(carbon_p50 * 0.021), "cost": "$96/t", "tone": "amber"},
            {"rank": 4, "title": "410 kW solar canopy on the parking deck",
             "detail": "cosmetic at this scale — shown for honesty",
             "tonnes": -round(carbon_p50 * 0.0013), "cost": "$412/t", "tone": "amber"},
        ],
        "heatmap": {
            "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            "hours": 24,
            "cells": heatmap(),
            "note": "Unmatched hours are red. The gap is winter evenings — exactly when the grid is dirtiest and the load is highest. This is the only honest way to show 24/7 matching.",
        },
        "export": [
            {"label": "CSV", "detail": "one row per hour, every column carrying its source id"},
            {"label": "JSON", "detail": "the ledger with method + model + dataset hashes"},
            {"label": "PDF", "detail": "methods appendix for a filing or a board pack"},
        ],
        "footer": "MODEL: OFF IN THIS VIEW · every figure computed by code from the ledger; the model only explains it",
    }


# ----------------------------------------------------------------------- main

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = read_rows()
    by_reg = {r["RegistryID"].strip(): r for r in rows}
    hero_ids = {h["fid"] for h in HEROES}

    index = build_index(rows, heroes=hero_ids)
    assert len(index) == 479, len(index)
    assert len({e["id"] for e in index}) == 479
    (OUT / "facilities.json").write_text(json.dumps(index, indent=1) + "\n")

    written = []
    for n, hero in enumerate(HEROES):
        payload = build_facility(hero, by_reg[hero["registry"]])
        arith = payload.pop("_arith")
        # INV-1 check: recomputed from the chain, not from the literal
        assert arith["base_t"] == tonnes(arith["mwh"], hero["rate"])

        carbon = next(c for c in payload["claims"] if c["metric"] == "carbon_t")
        assert carbon["p10"] < carbon["p50"] < carbon["p90"]

        # hero 3: block the carbon receipt (verification failure demo)
        if hero["registry"] == "110001914821":
            carbon["renderable"] = False
            carbon["blocked_reason"] = (
                "No renderable receipt: the permit quote failed substring verification "
                "(p.4 line 12 not found in the archived PDF, sha256 9b2e…e9b). "
                "The calculation exists; the evidence does not."
            )
            bad = carbon["citations"][0]
            bad["verified"] = False
            bad["quote"] = (
                "The facility shall not operate the emergency generators more than "
                "100 hours per year except during a declared grid emergency."
            )
            bad["matched_text"] = "100 hours per year"

        fid_slug = slug(hero["fid"])
        (OUT / f"fixture-facility-{fid_slug}.json").write_text(json.dumps(payload, indent=1) + "\n")
        written.append(f"fixture-facility-{fid_slug}.json")

        drawers = build_drawers(hero, {**payload, "_arith": arith})
        for claim_id, drawer in drawers:
            name = f"fixture-drawer-{fid_slug}-{slug(claim_id)}.json"
            (OUT / name).write_text(json.dumps(drawer, indent=1) + "\n")
            written.append(name)

        if hero["registry"] != "110001914821":  # blocked card → no scenario
            op = build_operator(hero, payload["facility"], carbon["p50"], 36.0 if n == 0 else 41.2)
            name = f"fixture-operator-{fid_slug}.json"
            (OUT / name).write_text(json.dumps(op, indent=1) + "\n")
            written.append(name)

    print(f"{len(index)} facilities indexed")
    for w in written:
        size = (OUT / w).stat().st_size
        print(f"  {w:64s} {size/1024:6.1f} KB")


if __name__ == "__main__":
    main()
