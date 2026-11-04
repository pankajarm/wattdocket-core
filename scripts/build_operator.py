"""Build the operator payload — from real inputs only, or not at all.

The review verdict on the operator bench was blunt: a 24/7 matched heatmap and
market-based accounting resting on invented meter data is the first thing a
hostile reader attacks, because it is the one screen claiming to be a compliance
record while being fiction underneath.

So this builder computes what the sources actually support and explicitly
withholds the rest:

  location-based carbon   real  (capacity x utilisation x eGRID factor)
  hourly shape            real  (ERCOT 5-minute fuel mix, where it exists)
  generator capacity      real  (state air permit, where it was read)
  matched 24/7 %          withheld — needs the operator's own hourly load
  market-based carbon     withheld — needs the operator's EAC/PPA register
  water against permit    withheld — no permit limit, no operational reading

Withheld means an empty array and a sentence saying why, never a placeholder
number. `synthetic_meter_data` is false and stays false: if this file ever
contains an invented hour, it must say so.

    python scripts/build_operator.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "core"))

OUT = ROOT / "site" / "api" / "operator"


def slug(value: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def _method(card: dict, carbon: dict) -> dict:
    """The factor set and the arithmetic, so an exported file can be re-derived."""
    grid = card["grid"]
    return {
        "factor_set_id": grid.get("factor_set_id"),
        "factor_set_sha256": grid.get("factor_set_sha256"),
        "subregion": grid.get("subregion"),
        "subregion_name": grid.get("subregion_name"),
        "rate_lb_per_mwh": grid.get("rate_lb_per_mwh"),
        "binding_method": grid.get("binding_method"),
        "calc_id": carbon.get("calc_id"),
        "chain": carbon.get("chain") or [],
        "transforms": carbon.get("transforms") or [],
    }


def _sources(card: dict) -> list[dict]:
    """Every public record the bench leans on, one row per cited passage.

    Claim citations come from the card verbatim; permit facts and limits carry
    their own page and quote. Nothing here is re-stated in our words.
    """
    fac = card["facility"]
    out: list[dict] = []
    for c in card.get("claims", []):
        for cit in c.get("citations") or []:
            out.append({
                "supports": c.get("metric"),
                "publisher": cit.get("publisher"),
                "document": cit.get("dataset") or cit.get("document"),
                "locator": (f"field {cit['field']}" if cit.get("field")
                            else f"p.{cit.get('page')}"),
                "quote": cit.get("quote"),
                "doc_sha256": cit.get("doc_sha256") or cit.get("document_sha256"),
                "url": cit.get("url"),
                "verified": bool(cit.get("verified")),
            })
    # A permit fact a claim already cites is the same passage; list it once,
    # under the claim, where the verifier's mark is.
    cited = {r["quote"] for r in out}
    permit = fac.get("permit") or {}
    for f in permit.get("facts", []):
        if f.get("quote") in cited:
            continue
        out.append({
            "supports": f.get("metric"),
            "publisher": permit.get("authority"),
            "document": f"permit {permit.get('number')}",
            "locator": f"p.{f.get('page')}",
            "quote": f.get("quote"),
            "doc_sha256": permit.get("sha256"),
            "url": permit.get("url"),
            "verified": False,
        })
    va_equip = fac.get("permit_equipment") or {}
    va_url = fac.get("permit_url")
    if va_equip.get("quote"):
        out.append({
            "supports": "permit_equipment_mw",
            "publisher": "Virginia DEQ",
            "document": f"registration {va_equip.get('registration')}",
            "locator": f"p.{va_equip.get('page')}",
            "quote": va_equip.get("quote"),
            "doc_sha256": None,
            "url": va_url,
            "verified": False,
        })
    for lim in fac.get("permit_limits") or []:
        out.append({
            "supports": lim.get("metric"),
            "publisher": "Virginia DEQ",
            "document": f"registration {lim.get('registration')}",
            "locator": f"p.{lim.get('page')}",
            "quote": lim.get("quote"),
            "doc_sha256": None,
            "url": lim.get("url") or va_url,
            "verified": False,
        })
    return out


def build(card: dict) -> dict:
    fac = card["facility"]
    grid = card["grid"]
    claims = {c["metric"]: c for c in card["claims"]}
    carbon = claims.get("carbon_t", {})
    diesel = claims.get("diesel_mw", {})
    permit = fac.get("permit") or {}

    tonnes = carbon.get("value")
    tonnes = tonnes if isinstance(tonnes, (int, float)) else None
    rate = grid.get("rate_lb_per_mwh")

    # ---- methods: one real, two withheld, each with the reason ------------
    methods = []
    if tonnes is not None:
        methods.append({
            "key": "location",
            "label": "Location-based",
            "tonnes": round(tonnes, 0),
            "note": (f"{fac.get('city')}, {fac.get('state')} grid factor "
                     f"{rate} lb CO2e/MWh (eGRID2023, {grid.get('subregion')})"),
            "tone": "teal",
        })
    methods.append({
        "key": "market",
        "label": "Market-based",
        "tonnes": 0,
        "note": ("withheld — requires the operator's EAC/PPA register, which is "
                 "not public. Not estimated."),
        "tone": "amber",
    })
    methods.append({
        "key": "hourly",
        "label": "24/7 hourly matched",
        "tonnes": 0,
        "note": ("withheld — requires the operator's hourly load and clean-energy "
                 "procurement. An invented profile is not a compliance record."),
        "tone": "amber",
    })

    # ---- compliance: only what a permit or a published figure states ------
    compliance = []

    gen_fact = next((f for f in permit.get("facts", [])
                     if f["metric"] == "backup_generator_count"), None)
    rating_fact = next((f for f in permit.get("facts", [])
                        if f["metric"] == "generator_unit_rating_kw"), None)

    if gen_fact:
        compliance.append({
            "key": "generators",
            "label": "Backup generators",
            "value": f"{int(gen_fact['value']):,}",
            "unit": "units",
            "sub": (f"TCEQ permit {permit.get('number')} p.{gen_fact.get('page')} — "
                    "installed capacity, not run hours"),
            "pct": 100,
            "mark_pct": None,
            "tone": "teal",
        })

    diesel_value = None
    if isinstance(diesel.get("value"), (int, float)):
        diesel_value = float(diesel["value"])
    if diesel_value:
        compliance.append({
            "key": "diesel_mw",
            "label": "Backup diesel capacity",
            "value": f"{diesel_value:,.0f}",
            "unit": "MW",
            "sub": ("derived from the permit's unit count x rating; a floor, "
                    "because the permit states 'greater than'"),
            "pct": 100,
            "mark_pct": None,
            "tone": "blue",
        })

    # If a permit cap exists, say so here and point at the envelope: "withheld"
    # next to a visible 500 h/yr ceiling reads like a contradiction unless the
    # row explains that the ceiling is not a measurement.
    _cap = None
    for _l in (fac.get("permit_limits") or []):
        if _l["metric"] == "generator_hours_limit":
            _cap = _l["value"]
    compliance.append({
        "key": "generator_hours",
        "label": "Generator run hours",
        "value": "—",
        "unit": "hr/yr",
        "sub": (
            f"withheld — no operating record exists; the permit caps {_cap:,.0f} h/yr "
            f"per generator, which is a ceiling, not a measurement (see the envelope)"
            if _cap else
            "withheld — a permitted limit is not an operating record, and no state "
            "publishes actual run hours for this site"),
        "pct": 0,
        "mark_pct": None,
        "tone": "amber",
    })
    compliance.append({
        "key": "water",
        "label": "Water against permit",
        "value": "—",
        "unit": "gal/day",
        "sub": ("withheld — we found no facility-level operational water figure "
                "for this site to compare against a limit"),
        "pct": 0,
        "mark_pct": None,
        "tone": "amber",
    })

    peak = {
        "key": "peak",
        "label": "Reference peak load",
        "value": f"{claims.get('power_mw', {}).get('value', '—')}",
        "unit": "MW",
        "sub": "reference sizing used for every card in this catalogue",
        "pct": 50,
        "mark_pct": None,
        "tone": "teal",
    }

    # ---- the permit envelope: two cited numbers, and what they imply -------
    # This is the only place the two faces genuinely meet: the public card
    # carries what the state caps, the operator bench asks what that means for
    # a year of operation. Every figure below is arithmetic on permit numbers,
    # and the one assumption is labelled as an assumption.
    envelope = None
    gens = next((f for f in permit.get("facts", [])
                 if f["metric"] == "backup_generator_count"), None)
    rating = next((f for f in permit.get("facts", [])
                   if f["metric"] == "generator_unit_rating_kw"), None)

    limits = fac.get("permit_limits") or []
    # Prefer the total run-hours cap. MCRT is a narrower allowance for scheduled
    # testing and would understate the envelope if used as the only ceiling.
    hours_cap = next((l["value"] for l in limits
                      if l["metric"] == "generator_hours_limit"), None)
    mcrt_cap = next((l["value"] for l in limits
                     if l["metric"] == "mcrt_hours_limit"), None)
    if hours_cap is None:
        hours_cap = mcrt_cap
    fuel_cap = next((l["value"] for l in limits
                     if l["metric"] == "fuel_throughput_limit"), None)

    # Installed capacity can come from a Texas permit's equipment facts or from a
    # Virginia permit's equipment table; whichever this facility has.
    va_equip = fac.get("permit_equipment") or {}
    if not diesel_value and va_equip.get("mw_total"):
        diesel_value = float(va_equip["mw_total"])

    if diesel_value and hours_cap:
        permitted_mwh = diesel_value * hours_cap          # MW x hours = MWh
        envelope = {
            "installed_mw": diesel_value,
            "hours_cap": hours_cap,
            "permitted_mwh": round(permitted_mwh, 0),
            "rows": [
                {"label": "Installed backup capacity",
                 "value": f"{diesel_value:,.1f} MW",
                 "sub": (f"{va_equip['units']} generators summed from the permit's "
                         f"equipment table (p.{va_equip.get('page')})"
                         if va_equip else "permit generator count x unit rating")},
                {"label": "Permitted hours",
                 "value": f"{hours_cap:,.0f} h/yr",
                 "sub": "per generator, per the same permit"},
                {"label": "Maximum permitted diesel generation",
                 "value": f"{permitted_mwh:,.0f} MWh/yr",
                 "sub": "capacity x permitted hours — arithmetic, not a forecast"},
            ],
            "binding": None,
            "note": ("This is a ceiling, not a prediction. A site permitted for "
                     f"{hours_cap:,.0f} hours may run zero."),
        }
        if fuel_cap and permitted_mwh:
            implied_gal_per_mwh = fuel_cap / permitted_mwh
            # A large diesel genset burns roughly 70 gal/MWh at full load, and
            # always more at part load — never less. So an implied rate far below
            # that means the fuel cap and the equipment do not describe the same
            # fleet: usually the cap belongs to one engine group or a fire pump
            # while the equipment table sums the whole campus.
            #
            # The first version printed the arithmetic anyway and produced
            # "hours actually reachable: 1 h/yr" for a 200 MW campus. Publishing
            # that would be worse than publishing nothing, so the comparison is
            # refused when the two numbers cannot be reconciled.
            # Both bounds. A one-sided floor admitted a cap implying 583 gal/MWh —
            # eight times the rule of thumb — which is just as impossible in the
            # other direction and was published with a confident "hours binds".
            PLAUSIBLE_MIN = 40.0        # gal/MWh, deliberately generous
            PLAUSIBLE_MAX = 200.0
            if not PLAUSIBLE_MIN <= implied_gal_per_mwh <= PLAUSIBLE_MAX:
                envelope["inconsistent"] = True
                envelope["implied_gal_per_mwh"] = round(implied_gal_per_mwh, 1)
                envelope["note"] = (
                    f"The permit states {fuel_cap:,.0f} gal/yr for this site and "
                    f"{hours_cap:,.0f} permitted hours across {diesel_value:,.1f} MW. "
                    f"Those cannot both describe the same fleet — using the hours "
                    f"cap would imply {implied_gal_per_mwh:,.1f} gal/MWh against a "
                    f"~70 gal/MWh floor. Most likely the fuel cap covers one engine "
                    f"group while the equipment table sums the campus. No envelope "
                    f"is claimed until that is resolved against the permit text.")
                envelope["rows"] = envelope["rows"][:3]
            else:
                RULE_GAL_PER_MWH = 70.0
                effective = fuel_cap / (RULE_GAL_PER_MWH * diesel_value)
                # Compare with tolerance: at exactly the rule-of-thumb burn rate the
                # two caps coincide, and calling that "fuel binds first" invents a
                # constraint that is not there.
                binding = "fuel" if effective < hours_cap - 0.5 else (
                    "hours" if effective > hours_cap + 0.5 else "neither")
                envelope["binding"] = binding
                envelope["implied_gal_per_mwh"] = round(implied_gal_per_mwh, 1)
                envelope["effective_hours"] = round(effective, 0)
                envelope["assumption"] = (
                    f"assumes ~{RULE_GAL_PER_MWH:.0f} gal/MWh at full load; a "
                    f"different burn rate moves this figure proportionally")
                envelope["rows"].append({
                    "label": "Fuel cap",
                    "value": f"{fuel_cap:,.0f} gal/yr",
                    "sub": "permit fuel throughput limit",
                })
                envelope["rows"].append({
                    "label": "Fuel implied by using the hours cap fully",
                    "value": f"{implied_gal_per_mwh:,.0f} gal/MWh",
                    "sub": (f"a large genset needs ~{RULE_GAL_PER_MWH:.0f} gal/MWh, "
                            f"so the {binding} cap is the binding one"),
                })
                if binding == "fuel":
                    envelope["rows"].append({
                        "label": "Hours actually reachable under the fuel cap",
                        "value": f"{effective:,.0f} h/yr",
                        "sub": (f"fuel cap / ({RULE_GAL_PER_MWH:.0f} gal/MWh x "
                                f"{diesel_value:,.1f} MW) — an estimate, not a permit term"),
                    })
                envelope["note"] = {
                    "fuel": (f"Ceilings, not predictions. The fuel cap is the "
                             f"constraint that binds first: at "
                             f"{implied_gal_per_mwh:,.0f} gal/MWh the site can reach "
                             f"about {effective:,.0f} of its {hours_cap:,.0f} "
                             f"permitted hours."),
                    "hours": (f"Ceilings, not predictions. The hours cap binds first: "
                              f"the fuel budget would allow {effective:,.0f} h, more "
                              f"than the permit's {hours_cap:,.0f}."),
                    "neither": (f"Ceilings, not predictions. The two caps coincide at "
                                f"roughly {implied_gal_per_mwh:,.0f} gal/MWh, so "
                                f"neither one binds alone."),
                }[binding]

    # ---- levers: arithmetic on the real hourly series, where one exists ---
    levers = []
    hourly = card.get("hourly") or {}
    means = [m for m in (hourly.get("means_lb_per_mwh") or [])
             if isinstance(m, (int, float))]
    if len(means) >= 12 and tonnes:
        ordered = sorted(range(len(means)), key=lambda i: means[i])
        n = max(1, len(means) // 6)             # a sixth of the day
        clean = ordered[:n]
        dirty = ordered[-n:]
        clean_mean = sum(means[i] for i in clean) / n
        dirty_mean = sum(means[i] for i in dirty) / n
        share = n / len(means)
        saved = tonnes * share * (1 - clean_mean / dirty_mean)
        levers.append({
            "rank": 1,
            "title": f"Move {int(round(share * 100))}% of run hours out of the dirtiest hours",
            "detail": (f"{hourly.get('day')} ranged {means[ordered[0]]:.0f}-"
                       f"{means[ordered[-1]]:.0f} lb/MWh; the {n} dirtiest hours "
                       f"averaged {dirty_mean:.0f} against {clean_mean:.0f} in the cleanest"),
            "tonnes": round(saved, 0),
            "cost": "no capital cost — a scheduling change",
            "tone": "teal",
        })
    levers.append({
        "rank": len(levers) + 1,
        "title": "Procure matched clean energy for the load",
        "detail": ("not quantified here: the reduction depends on the instrument "
                   "and its vintage, which the operator holds and we do not"),
        "tonnes": 0,
        "cost": "PPA / EAC dependent — cannot be costed from public data",
        "tone": "amber",
    })

    return {
        "as_of": card.get("as_of", ""),
        "calc_version": "wattdocket.operator@0.1.0",
        "facility_id": fac.get("id"),
        "facility_label": f"{fac.get('name')} — {fac.get('city')}, {fac.get('state')}",
        "synthetic_meter_data": False,
        "methods": methods,
        "matching_note": (
            "Two of the three accounting methods are withheld, not estimated. The "
            "public record contains this site's grid factor and, where a permit was "
            "read, its installed generator capacity — it does not contain the hourly "
            "load or the energy attribute register that market-based and 24/7 "
            "numbers are built from."),
        "matched_pct": 0,
        "compliance": compliance,
        "peak": peak,
        "levers": levers,
        "heatmap": {
            "months": [],
            "hours": 0,
            "cells": [],
            "note": ("no matched-hours heatmap: it would require an hourly clean-energy "
                     "profile this build does not have. Withheld rather than simulated."),
        },
        "permit_envelope": envelope,
        "method": _method(card, carbon),
        "sources": _sources(card),
        "export": [
            {"label": "Location-based tCO2e/yr",
             "detail": "in the file, with its factor set, hash and arithmetic"},
            {"label": "Permit capacity and caps",
             "detail": ("in the file, each with its permit page and quote"
                        if permit.get("facts") or fac.get("permit_limits")
                        or fac.get("permit_equipment")
                        else "no permit was read for this facility")},
            {"label": "Hourly load, gen-set run log, water meter",
             "detail": "operator-held; exported as withheld, never as a number"},
        ],
        "footer": ("Built from public sources only. Where a figure would require "
                   "operator-held data, the bench says so and shows nothing."),
    }


def main() -> None:
    cards = json.loads((ROOT / "site" / "data" / "cards.json").read_text())["cards"]
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for fid, entry in cards.items():
        card_path = ROOT / "site" / "data" / "cards" / f"{fid}.json"
        if not card_path.exists():
            continue
        card = json.loads(card_path.read_text())
        (OUT / slug(fid)).write_text(json.dumps(build(card)))
        n += 1
    print(f"operator payloads: {n} -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
