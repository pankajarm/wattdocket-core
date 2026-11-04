"""Virginia permit obligations, and the two honesty rules they impose.

Rule one: the text did not come from a PDF this process hashed. It came through
an extraction service because the Commonwealth's origin blocks this host. A
citation that did not say so would be overclaiming the chain of custody.

Rule two: a permitted limit is not an operating record. "100 hours per year" is
what the site may do. Printing it next to a card that says "backup diesel" and
letting a reader merge the two would be the same class of error as the
utilised-capacity mistake in the carbon path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VAObiligation:
    metric: str
    value: float
    unit: str
    page: int | None
    quote: str
    url: str
    registration: str
    retrieved_via: str

    def as_claim_extra(self) -> dict:
        return {
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "page": self.page,
            "quote": self.quote,
            "registration": self.registration,
            "url": self.url,
            "retrieved_via": self.retrieved_via,
            "is_permitted_limit": True,
        }


LABELS = {
    "generator_hours_limit": "Generator run hours cap",
    "fuel_throughput_limit": "Diesel fuel throughput cap",
    "fuel_sulfur_limit": "Fuel sulfur limit",
}

UNITS = {
    "generator_hours_limit": "hours/year per generator",
    "fuel_throughput_limit": "gallons/year",
    "fuel_sulfur_limit": "% by shipment",
}


def load(path: str | Path) -> dict[str, list[VAObiligation]]:
    """{registration: [obligations]} from the fetched facts file."""
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    out: dict[str, list[VAObiligation]] = {}
    for reg, rec in data.items():
        items = []
        for o in rec.get("obligations") or []:
            cit = o.get("citation") or {}
            items.append(VAObiligation(
                metric=o["metric"],
                value=float(o["value"]),
                unit=o["unit"],
                page=cit.get("page"),
                quote=cit.get("quote", ""),
                url=rec.get("url", ""),
                registration=reg,
                retrieved_via=rec.get("retrieved_via", ""),
            ))
        if items:
            out[reg] = items
    return out


def for_registrations(facts: dict[str, list[VAObiligation]],
                      registrations: list[str]) -> list[VAObiligation]:
    """Obligations of the given permits (`facts` is `load()`'s result), in file order.

    The join key is the registration the card already carries, so whatever
    matched the card to the registry (name or number) also decides which caps
    it shows; there is no second, looser join here.
    """
    wanted = set(registrations)
    out: list[VAObiligation] = []
    for reg, obligations in facts.items():
        if reg not in wanted:
            continue
        for o in obligations:
            sig = (o.metric, o.value, o.unit, o.registration)
            if any((x.metric, x.value, x.unit, x.registration) == sig for x in out):
                continue
            out.append(o)
    return out


def load_raw(path: str | Path) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def equipment_for_registrations(raw: dict, registrations: list[str]) -> dict | None:
    """The equipment row for the given permits (the last parsed one), or None.

    `raw` is the facts file as `load_raw()` returns it.
    """
    wanted = set(registrations)
    found = None
    for reg, rec in raw.items():
        rows = rec.get("equipment") or []
        if reg in wanted and rows:
            found = _equipment_row(reg, rows[0])
    return found


def _equipment_row(reg: str, e: dict) -> dict:
    return {
        "units": int(e["value"]),
        "mw_total": float(e["mw_total"]),
        "rated_ekw": float(e["rated_ekw"]),
        "ratings_ekw": e.get("ratings_ekw") or [],
        "page": (e.get("citation") or {}).get("page"),
        "quote": (e.get("citation") or {}).get("quote"),
        "registration": reg,
    }


def describe(o: VAObiligation) -> str:
    label = LABELS.get(o.metric, o.metric)
    if o.metric == "fuel_sulfur_limit":
        return f"{label}: {o.value}% by shipment"
    return f"{label}: {o.value:,.0f} {UNITS.get(o.metric, o.unit)}"
