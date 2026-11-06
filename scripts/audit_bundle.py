"""Produce an audit bundle: every number the product shows, with its derivation.

The board is being asked to check the numbers one by one, so they must be handed
the numbers as the product emits them — read from the committed artifacts, not
reconstructed from anyone's memory. Where a figure is derived, the derivation
travels with it; where a figure is withheld, the absence is recorded as a row,
because "we chose not to say" is also a claim that needs checking.

    python scripts/audit_bundle.py --out /tmp/audit.md
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import statistics
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).parent.parent


def cards() -> dict[str, dict]:
    return {pathlib.Path(f).stem: json.loads(pathlib.Path(f).read_text())
            for f in glob.glob(str(ROOT / "artifacts" / "cards" / "*.json"))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/audit.md")
    args = ap.parse_args()

    all_cards = cards()
    index = json.loads((ROOT / "artifacts" / "cards.json").read_text())
    cal = json.loads((ROOT / "artifacts" / "camd_calibration.json").read_text())
    va = json.loads((ROOT / "artifacts" / "va_permit_facts.json").read_text())
    permits = json.loads((ROOT / "artifacts" / "permits.json").read_text())

    L: list[str] = []
    w = L.append

    w("# WattDocket — number-by-number audit pack\n")
    w(f"Generated from committed artifacts. Cards: {len(all_cards)}. "
      f"Index: {index['count']} records / {index['sites']} sites.\n")

    # ---------------------------------------------------------------- methods
    w("## 1. METHODS IN USE\n")
    hero = all_cards["echo_110072155814"]
    carbon = next(c for c in hero["claims"] if c["metric"] == "carbon_t")
    w("### 1.1 Annual carbon (the headline number)\n")
    w("```")
    for step in carbon["chain"]:
        w(step)
    w("```")
    w("Transforms claimed:\n")
    for t in carbon["transforms"]:
        w(f"- {t}")
    w("Sensitivity claimed:\n")
    for s in carbon["sensitivity"]:
        w(f"- {s['label']}: {s['delta']}")
    w(f"\nBadge: {carbon['badge']} · provisional: {carbon['provisional']} · "
      f"range: {carbon['range']}\n")

    w("### 1.2 Grid binding\n")
    g = hero["grid"]
    w(f"- method: `{g['binding_method']}`")
    w(f"- subregion: {g['subregion']} ({g.get('subregion_name')})")
    w(f"- rate: {g['rate_lb_per_mwh']} lb CO2e/MWh")
    w(f"- factor set: {g['factor_set_id']} sha256 {str(g.get('factor_set_sha256'))[:16]}…")
    w(f"- intensity state: {g['intensity_state']}\n")

    w("### 1.3 Hourly intensity (hero only)\n")
    h = hero.get("hourly") or {}
    w(f"- ba {h.get('ba')} · day {h.get('day')} · intervals {h.get('intervals')}")
    w(f"- min {h.get('min')} max {h.get('max')} ratio {h.get('ratio')}")
    w(f"- storage excluded: {h.get('storage_excluded_mw')} MW")
    w(f"- state {h.get('intensity_state')} · note: {h.get('note')}\n")

    # ---------------------------------------------------------------- numbers
    w("## 2. THE NUMBERS, AS EMITTED\n")
    per_metric: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for fid, card in all_cards.items():
        for cl in card["claims"]:
            v = cl.get("value")
            if isinstance(v, (int, float)):
                per_metric[cl["metric"]].append((fid, float(v)))

    for metric, rows in sorted(per_metric.items()):
        vals = [v for _, v in rows]
        w(f"### {metric} — {len(rows)} cards with a numeric value\n")
        w(f"- min {min(vals):,.2f} · median {statistics.median(vals):,.2f} · "
          f"max {max(vals):,.2f}")
        if len(set(round(v, 6) for v in vals)) == 1:
            w(f"- **constant across every card** at {vals[0]:,.2f}")
        w("")

    # ---------------------------------------------------------------- withheld
    w("## 3. WITHHELD — counted, not hidden\n")
    withheld = Counter()
    for card in all_cards.values():
        for cl in card["claims"]:
            if not isinstance(cl.get("value"), (int, float)):
                withheld[(cl["metric"], str(cl.get("value")))] += 1
    for (metric, val), n in sorted(withheld.items()):
        w(f"- {metric}: `{val}` on {n} cards")
    w("")

    # ---------------------------------------------------------------- permits
    w("## 4. PERMIT-DERIVED FIGURES\n")
    w(f"- Texas permits read: {len(permits['records'])} · matched to cards: "
      f"{len(permits['matches'])}")
    for fid, rec in permits["matches"].items():
        w(f"  - {fid} ← {rec['file']} ({len(rec['equipment'])} equipment facts)")
    w(f"- Virginia permits fetched: {len(va)} · with obligations: "
      f"{sum(1 for v in va.values() if v['obligations'])} · with equipment table: "
      f"{sum(1 for v in va.values() if v.get('equipment'))}")
    va_caps = sum(1 for c in all_cards.values() if c["facility"].get("permit_limits"))
    va_equip = sum(1 for c in all_cards.values() if c["facility"].get("permit_equipment"))
    w(f"- cards carrying VA caps: {va_caps} · VA equipment: {va_equip}")
    multi = sum(1 for c in all_cards.values()
                if (c["facility"].get("site_permit_count") or 1) > 1)
    w(f"- cards at multi-permit sites: {multi}\n")

    # sample envelopes
    op_dir = ROOT / "site" / "api" / "operator"
    envs = []
    for f in glob.glob(str(op_dir / "*")):
        d = json.loads(pathlib.Path(f).read_text())
        if d.get("permit_envelope"):
            envs.append((pathlib.Path(f).name, d["permit_envelope"]))
    w(f"### 4.1 Permit envelopes — {len(envs)} operator payloads\n")
    for name, e in envs[:3]:
        w(f"**{name}** — installed {e['installed_mw']} MW × {e['hours_cap']:.0f} h "
          f"= {e['permitted_mwh']:,.0f} MWh ceiling; binding: {e['binding']}; "
          f"effective hours {e.get('effective_hours')}")
        for r in e["rows"]:
            w(f"  - {r['label']}: {r['value']} — {r['sub']}")
        w(f"  - assumption: {e.get('assumption')}")
        w("")

    # ---------------------------------------------------------------- camd
    w("## 5. CALIBRATION RESULT (CAMD/CEMS)\n")
    w(f"- day {cal['day']} · state {cal['state']} · rows {cal['rows']} · "
      f"usable unit-hours {cal['usable_unit_hours']}")
    for fuel, f in cal["by_fuel"].items():
        t = cal["table"].get(fuel)
        w(f"- {fuel}: measured {f['lb_per_mwh']:,.0f} vs table "
          f"{t:,.0f} ({f['lb_per_mwh'] / t - 1:+.1%}) over {f['mwh']:,.0f} MWh")
    hh = cal["hourly_fossil_intensity"]
    if hh:
        lo, hi = min(hh.values()), max(hh.values())
        w(f"- fossil-only hourly intensity {lo:,.0f}–{hi:,.0f} lb/MWh "
          f"({hi / lo:.2f}x) — **not a grid intensity**")
    w("")

    # ---------------------------------------------------------------- claims about the world
    w("## 6. STATEMENTS THE PRODUCT MAKES ABOUT THE WORLD\n")
    w("These are the sentences a reader will check, independent of arithmetic:\n")
    w("1. \"479 records across 466 sites\" — ECHO rows vs distinct RegistryIDs.")
    w("2. \"93 cards carry Virginia DEQ caps\" — exact-name + same-state match.")
    w("3. \"The same 100 MW load is 3.77× dirtier in RFCW than NYUP\" — eGRID2023.")
    w("4. \"ERCOT swung 561→1,022 lb/MWh on 2026-10-26 (1.82×)\" — 288 intervals.")
    w("5. \"TCEQ permit 164110 says 2,055 hr/yr and 2,025 hr/yr\" — both cited.")
    w("6. \"The fuel cap binds before the hours cap\" — 70 gal/MWh rule of thumb.")
    w("7. \"Our defaults sit 23–35% below LBNL's 176 TWh\" — national check.")
    w("8. \"Water is not published; no US state requires facility-level reporting.\"")
    w("9. \"Facility type is unverified for 368 cards\" — NAICS 518210.")
    w("")

    out = pathlib.Path(args.out)
    out.write_text("\n".join(L))
    print(f"wrote {out} ({len(L)} lines, {out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
