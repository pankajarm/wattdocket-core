"""Rebuild the VA facts from cached permit text — no network.

The cache exists because the origin blocks this host. Once the text is on disk,
re-extraction is free, so the extractor can be fixed without re-fetching 188
documents through a rate-limited service.

    python scripts/rebuild_va_facts.py
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "core"))

from wattdocket.va_permits import (  # noqa: E402
    PermitDocument,
    QuoteNotFound,
    extract_equipment,
    extract_obligations,
)

CACHE = ROOT / "data" / "va_permits"
OUT = ROOT / "artifacts" / "va_permit_facts.json"
REGISTRY = ROOT / "data" / "deq_air_permits.csv"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def main() -> None:
    rows = list(csv.DictReader(REGISTRY.open()))
    by_reg = {}
    for r in rows:
        by_reg.setdefault(r["registration"], r)

    facts: dict[str, dict] = {}
    missing = 0
    for reg, row in by_reg.items():
        cached = CACHE / f"{slug(reg)}.txt"
        if not cached.exists():
            missing += 1
            continue
        doc = PermitDocument.from_text(cached.read_text(), row["permit_url"])
        try:
            obligations = extract_obligations(doc)
        except QuoteNotFound:
            obligations = []
        try:
            equipment = extract_equipment(doc)
        except QuoteNotFound:
            equipment = []
        facts[reg] = {
            "site": row["site"],
            "registration": reg,
            "county": row["county"],
            "issued": row["issued"],
            "program": row["program"],
            "url": row["permit_url"],
            "pages_claimed": doc.page_count_claimed,
            "pages_recovered": len(doc.pages),
            "obligations": obligations,
            "equipment": equipment,
            "retrieved_via": doc.retrieved_via,
        }

    OUT.write_text(json.dumps(facts, indent=1))
    with_equip = [v for v in facts.values() if v.get("equipment")]
    print(f"with an equipment table: {len(with_equip)}")
    with_oblig = [v for v in facts.values() if v["obligations"]]
    print(f"permits rebuilt from cache: {len(facts)} (missing text: {missing})")
    print(f"with at least one cited obligation: {len(with_oblig)}")

    per_metric: dict[str, int] = {}
    for v in facts.values():
        for o in v["obligations"]:
            per_metric[o["metric"]] = per_metric.get(o["metric"], 0) + 1
    for metric, n in sorted(per_metric.items(), key=lambda kv: -kv[1]):
        print(f"  {metric:26s} {n}")

    hours = sorted(o["value"] for v in facts.values() for o in v["obligations"]
                   if o["metric"] in ("mcrt_hours_limit", "generator_hours_limit"))
    if hours:
        print(f"hour caps: {len(hours)} found, range {hours[0]:.0f}-{hours[-1]:.0f}")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
