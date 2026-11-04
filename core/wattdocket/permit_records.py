"""Attach permit facts to the cards whose facility they describe.

The join is address-based, and it is exact. A permit names a street address and a
county; the catalogue has a street address and a zip. Matching on street number +
street name + state is strong enough to be safe and weak enough to work on real
spelling differences ("14720 Omicron Dr" vs "14720 OMICRON DR").

Nothing fuzzy, for the same reason the Virginia registry join is exact: a
near-match here would put another company's generators on this company's card.

    python -m wattdocket.permit_records --out ../artifacts/permits.json
"""

from __future__ import annotations

import json
import re
from argparse import ArgumentParser
from pathlib import Path

from .permits import PermitDocument, extract_equipment, facility_identity

_UNIT = re.compile(r"\b(street|st|drive|dr|road|rd|avenue|ave|boulevard|blvd|lane|ln|"
                   r"parkway|pkwy|court|ct|way|circle|cir|place|pl|highway|hwy)\b")


def _street_key(address: str) -> str | None:
    """<number><street name>, with the suffix word removed.

    Suffixes are the least reliable part: TCEQ writes "Dr", the registry writes
    "DRIVE", the post office writes "DR". Dropping it keeps the match on the two
    tokens that actually identify a building.
    """
    if not address:
        return None
    text = re.sub(r"[^A-Za-z0-9 ]+", " ", address).strip().lower()
    m = re.match(r"\s*(\d{1,6})\s+(.+)", text)
    if not m:
        return None
    number, rest = m.group(1), m.group(2)
    # Cut at the first label that starts a new field, then drop the suffix word.
    rest = re.split(r"\b(nearest city|county|project|permit|naics)\b", rest)[0]
    rest = _UNIT.sub(" ", rest)
    words = rest.split()
    # Three words after the suffix strip: enough to identify a building, short
    # enough to survive "Omicron Dr" vs "Omicron Drive Suite 100".
    return f"{number} {' '.join(words[:3])}".strip()


def records(permit_dir: Path) -> list[dict]:
    out = []
    for pdf in sorted(permit_dir.glob("*.pdf")):
        try:
            doc = PermitDocument.load(pdf)
        except Exception as exc:                       # noqa: BLE001
            out.append({"file": pdf.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        ident = facility_identity(doc)
        equipment = extract_equipment(doc)
        out.append({
            "file": pdf.name,
            "sha256": doc.sha256,
            "pages": doc.page_count,
            "identity": ident,
            "street_key": _street_key(ident.get("address", "")),
            "equipment": equipment,
        })
    return out


def attach(cards: dict[str, dict], permits: list[dict]) -> dict[str, dict]:
    """Return {facility_id: permit_record} for exact street matches."""
    by_street: dict[str, dict] = {}
    for p in permits:
        if p.get("street_key"):
            by_street.setdefault(p["street_key"], p)

    matched: dict[str, dict] = {}
    for fid, row in cards.items():
        key = _street_key(row.get("address_line") or row.get("address") or "")
        if key and key in by_street:
            matched[fid] = by_street[key]
    return matched


def main() -> None:
    ap = ArgumentParser()
    ap.add_argument("--permits", default="data/permits")
    ap.add_argument("--cards", default="artifacts/cards.json")
    ap.add_argument("--out", default="artifacts/permits.json")
    args = ap.parse_args()

    permits = records(Path(args.permits))
    ok = [p for p in permits if "identity" in p]
    print(f"permits read: {len(ok)} of {len(permits)}")
    for p in ok:
        who = p["identity"].get("company") or p["identity"].get("county") or "?"
        print(f"  {p['file'][:34]:34s} {p['pages']:>3}p  key={p['street_key']!s:22s} {who[:34]}")

    # The index is deliberately thin — no street address — so the addresses come
    # from the full card files. Reading them all costs a second and removes the
    # need to duplicate address data into the index.
    cards_dir = Path(args.cards).parent / "cards"
    addresses: dict[str, dict] = {}
    if cards_dir.is_dir():
        for f in cards_dir.glob("*.json"):
            card = json.loads(f.read_text())
            fac = card["facility"]
            addresses[fac["id"]] = {"address_line": fac.get("address"),
                                    "name": fac.get("name")}
    index = json.loads(Path(args.cards).read_text())["cards"]
    matched = attach(addresses, permits)
    for fid, p in matched.items():
        print(f"matched: {fid} ({addresses[fid]['name'][:28]}) <- {p['file']}")

    out = {
        "source": "state air permits, text-extracted; page numbers derived from search",
        "records": permits,
        "matches": {fid: {"file": p["file"], "sha256": p["sha256"],
                          "identity": p["identity"], "equipment": p["equipment"]}
                    for fid, p in matched.items()},
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
