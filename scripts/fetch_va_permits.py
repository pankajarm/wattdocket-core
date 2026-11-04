"""Fetch Virginia permit text for the sites in our catalogue, and cache it.

The Commonwealth's PDFs are behind an Akamai rule that 403s this host, so text
comes through an extraction service. Two consequences, both handled here:

  - it is slower and rate-limited, so results are cached and the run is resumable;
  - the provenance is not "a PDF we hashed", so every record carries
    `retrieved_via` and the card shows it.

    python scripts/fetch_va_permits.py --limit 15
    python scripts/fetch_va_permits.py            # all sites in the registry
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "core"))

from wattdocket.va_permits import (  # noqa: E402
    QuoteNotFound,
    extract_obligations,
    fetch_permit,
)

CACHE = ROOT / "data" / "va_permits"
OUT = ROOT / "artifacts" / "va_permit_facts.json"


def slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="data/deq_air_permits.csv")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pause", type=float, default=1.5)
    ap.add_argument("--only", default="", help="substring filter on site name")
    args = ap.parse_args()

    rows = list(csv.DictReader((ROOT / args.registry).open()))
    seen: set[str] = set()
    targets = []
    for r in rows:
        key = r["permit_url"]
        if key in seen or not key:
            continue
        if args.only and args.only.lower() not in r["site"].lower():
            continue
        seen.add(key)
        targets.append(r)
    if args.limit:
        targets = targets[: args.limit]

    CACHE.mkdir(parents=True, exist_ok=True)
    facts: dict[str, dict] = {}
    if OUT.exists():
        facts = json.loads(OUT.read_text())

    ok = fail = 0
    for i, row in enumerate(targets, 1):
        site = row["site"]
        key = row["registration"]
        cached = CACHE / f"{slug(key)}.txt"
        url = row["permit_url"]

        try:
            if cached.exists():
                raw = cached.read_text()
            else:
                raw = fetch_text_cached(url, cached, args.pause)
            doc = _doc_from(raw, url)
            obligations = extract_obligations(doc)
            facts[key] = {
                "site": site,
                "registration": key,
                "county": row["county"],
                "issued": row["issued"],
                "program": row["program"],
                "url": url,
                "pages_claimed": doc.page_count_claimed,
                "pages_recovered": len(doc.pages),
                "obligations": obligations,
                "retrieved_via": doc.retrieved_via,
            }
            ok += 1
            summary = ", ".join(
                f"{o['metric']}={o['value']:g}" for o in obligations) or "no pattern matched"
            print(f"  [{i:>3}/{len(targets)}] {key:>9}  {site[:34]:34s} {len(obligations)} → {summary[:70]}")
        except QuoteNotFound as exc:
            fail += 1
            print(f"  [{i:>3}/{len(targets)}] {key:>9}  {site[:34]:34s} QUOTE FAILED: {exc}")
        except Exception as exc:                       # noqa: BLE001
            fail += 1
            print(f"  [{i:>3}/{len(targets)}] {key:>9}  {site[:34]:34s} ERROR {type(exc).__name__}: {exc}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(facts, indent=1))
    with_oblig = sum(1 for v in facts.values() if v["obligations"])
    print(f"\nfetched ok: {ok} | failed: {fail} | cached|total: {len(facts)}")
    print(f"permits with at least one cited obligation: {with_oblig}")
    print(f"wrote {OUT.relative_to(ROOT)}")


def fetch_text_cached(url: str, cache: pathlib.Path, pause: float) -> str:
    from wattdocket.va_permits import fetch_text
    raw = fetch_text(url)
    cache.write_text(raw)
    time.sleep(pause)
    return raw


def _doc_from(raw: str, url: str):
    from wattdocket.va_permits import PermitDocument
    return PermitDocument.from_text(raw, url)


if __name__ == "__main__":
    main()
