"""Trusted facts for the Muse Spark explanation of a production card.

Each production release publishes `explain.json`: for every card, a short list
of plain-English facts and the card's deterministic summary. The explain
function reads this file from the same immutable release the reader's page is
pinned to, so the model only ever sees server-owned text, and every numeral in
its reply must already appear in these facts (see `wattdocket.prose`).

A fact is written only from a field the card itself shows. Absences are stated
only where the card states them, never inferred from a missing field.
"""
from __future__ import annotations

import math

MAX_FACTS = 10
MAX_FACT_CHARS = 400


def _number(value: object, places: int = 0) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return f"{value:,.{places}f}"


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value or None


def card_facts(card: dict) -> list[str]:
    """The facts a reader can check on this card, in the card's own terms."""
    facility = card.get("facility") or {}
    facts: list[str] = []

    name = _text(facility.get("name")) or "This record"
    place = ", ".join(x for x in (_text(facility.get("city")), _text(facility.get("state"))) if x)
    source = _text(facility.get("source_label")) or "a public source"
    facts.append(f"{name}{' in ' + place if place else ''} is listed by {source}.")
    class_label = _text(facility.get("class_label"))
    if class_label:
        facts.append(f"Classification on this card: {class_label}.")

    quality = facility.get("location_quality") or {}
    grid = card.get("grid") or {}
    rate = _number(grid.get("rate_lb_per_mwh"), 1)
    if quality.get("map_eligible") is False:
        facts.append("The reported location needs review, so no grid region is assigned to this record.")
    elif grid.get("subregion") and rate is not None:
        region = _text(grid.get("subregion_name")) or grid["subregion"]
        year = _text(grid.get("data_vintage"))
        facts.append(
            f"The location falls in the EPA eGRID {region} region, whose official "
            f"{year + ' ' if year else ''}annual grid average is {rate} pounds of carbon dioxide "
            "equivalent per megawatt-hour; this describes the regional grid, not this facility.")

    limits = facility.get("permit_limits") or []
    if isinstance(limits, list) and limits:
        regs = sorted({str(x.get("registration")) for x in limits if isinstance(x, dict) and x.get("registration")})
        facts.append(
            f"{len(limits)} quoted permit condition{'s' if len(limits) != 1 else ''} from Virginia DEQ "
            f"registration{'s' if len(regs) != 1 else ''} {', '.join(regs)} "
            f"{'are' if len(limits) != 1 else 'is'} attached; legal effect may be unresolved.")
    documents = facility.get("documents") or []
    if isinstance(documents, list) and documents:
        facts.append(f"{len(documents)} Texas permit document{'s are' if len(documents) != 1 else ' is'} attached as evidence.")

    for claim in card.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("scope") == "scenario":
            continue
        value = claim.get("value")
        if claim.get("id") == "diesel-mw" and _number(value, 1) is not None and claim.get("citations"):
            facts.append(
                f"A cited permit equipment table lists {_number(value, 1)} megawatts of generator "
                "nameplate capacity; it is not a whole-site total or actual operating use.")

    unavailable = [claim.get("label") for claim in card.get("claims") or []
                   if isinstance(claim, dict) and claim.get("scope") != "scenario"
                   and claim.get("value") == "NOT_PUBLISHED" and _text(claim.get("label"))]
    if unavailable:
        facts.append("Not available in the collected public sources: "
                     + "; ".join(label.lower() for label in unavailable) + ".")

    for group in (card.get("disclosures") or [])[:2]:
        location = (group or {}).get("location") or {}
        label = _text(location.get("location_label"))
        operator = _text(location.get("operator"))
        if not label:
            continue
        rows = []
        for row in (location.get("observations") or [])[:3]:
            if not isinstance(row, dict):
                continue
            value = row.get("value")
            amount = _number(value, 0)
            if amount is not None and not float(value).is_integer():
                amount = _number(value, 1)
            year = row.get("data_year")
            if amount is None or not _text(row.get("label")) or not _text(row.get("unit")) or not isinstance(year, int):
                continue
            rows.append(f"{row['label'].lower()} of {amount} {row['unit']} for {year}")
        if rows:
            facts.append(
                f"{operator + ' reports' if operator else 'The publisher reports'} "
                f"{'; '.join(rows)} for the reporting location {label} "
                f"({_text(location.get('reporting_scope')) or 'location'} scope); "
                "this related total is not allocated to this record.")

    if any(isinstance(c, dict) and c.get("scope") == "scenario" for c in card.get("claims") or []):
        facts.append("Any calculator figures on the card are hypothetical scenarios, not facility measurements.")

    return [fact[:MAX_FACT_CHARS] for fact in facts[:MAX_FACTS]]


def explain_bundle(cards: dict, release_id: str, built_at: str) -> dict:
    """The release file the explain function reads: facts and fallback per card."""
    records = {}
    for key, card in cards.items():
        template = _text(((card.get("verdict") or {}).get("sentence")))
        if not template:
            raise ValueError(f"Card {key} has no deterministic summary")
        records[key] = {"facility_id": card["facility"]["id"], "facts": card_facts(card), "template": template}
    return {"release_id": release_id, "built_at": built_at, "model_input": "facts_only", "records": records}
