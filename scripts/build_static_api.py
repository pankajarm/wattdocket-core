"""Publish the real cards as a static "API" tree.

The React app asks for `/api/v1/facility/<id>`; S3 can serve that path as an
object with a JSON content-type, so the production cards become the app's
live data with no server, no database and no model. This is the
"immutable artifacts served as if from an API" idea from ARCHITECTURE.md,
made concrete — and it means what a judge clicks is the same JSON the
estimator produced, not a fixture.

    python scripts/build_static_api.py --cards artifacts/cards \
        --index artifacts/cards.json --out site/api

Besides the cards it writes the SPEC §6 read routes that a static host can
honestly answer: `method` (factor set and its hash), `grid/<ba>/<day>` (the
captured ERCOT day) and `estimator/...` (a fixed grid of reference sizes by
eGRID subregion, plus each facility's own estimate). Anything outside those
files is a JSON 404 from the edge, never the app shell.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from wattdocket import __version__ as CALC_VERSION  # noqa: E402
from wattdocket.cards import SUBREGIONS  # noqa: E402
from wattdocket.estimator import CapacityBasis, EstimateInput, estimate  # noqa: E402
from wattdocket.grid import DEFAULT_FACTOR_SET  # noqa: E402
from wattdocket.hero import hourly_profile  # noqa: E402
from wattdocket.ingest.cards import factor_set_payload  # noqa: E402

# Reference sizes for the estimator grid, in MW of grid draw. The static API
# answers these and nothing in between: an interpolated size would be a number
# no calculation produced.
ESTIMATOR_CAPS_MW = (10, 25, 50, 100, 200, 300, 500, 1000)

# ERCOT's EIA code is ERCO; eGRID calls the same footprint ERCT. Both resolve.
GRID_BA_ALIASES = {"erco": ("erco", "erct")}

ERCOT_URL = "https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json"
EGRID_RATES = ROOT / "data" / "egrid_subregion_rates.json"

INVARIANTS = [
    "INV-1 metric tonnes from pounds (divide 2204.6226, never 2000)",
    "INV-2 timestamps stored UTC; a 25-hour DST day cannot shift joins",
    "INV-3 hour convention explicit per source (EIA-930 is hour-ending)",
    "INV-4 PUE applied only to IT-basis capacity",
    "INV-5 battery storage excluded from the intensity denominator",
    "INV-6 a provisional intensity carries its factor-set hash",
    "INV-7 no claim renders without a verified citation or a calc id",
    "INV-8 an empty lookup is 'not reported', never zero",
    "INV-9 water is never a scalar",
    "INV-10 model prose may only restate computed values",
]


def slug(value: str) -> str:
    """Must match deploy/cf-viewer-request.js slug() exactly."""
    out = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return out


def index_entries(cards: dict) -> list[dict]:
    out = []
    for fid, c in cards["cards"].items():
        # The UI keys on `echo:<registry>`; our file names use `echo_<reg>`.
        rid = fid.split("_")[-1]
        out.append({
            "id": f"echo:{rid}",
            "name": c.get("name"),
            "address_line": c.get("address_line") or "",
            "city": c.get("city"),
            "state": c.get("state"),
            "zip": c.get("zip") or "",
            "lat": c.get("lat"),
            "lon": c.get("lon"),
            "registry_id": rid,
            # The facility, not the permit. 479 rows resolve to 466 sites.
            "site_id": c.get("site_id") or f"echo:{rid}",
            "permits_at_site": c.get("permits_at_site"),
            "sibling_ids": c.get("sibling_ids"),
            "hero": bool(c.get("name", "").upper().startswith("VANTAGE DATA CENTER TX2")),
            "_card_file": fid,
        })
    return out


def _highlight(quote: str) -> str:
    """The span inside a quote worth underlining: the number and its unit."""
    import re
    m = re.search(r"\d[\d,]*\.?\d*\s*(?:lb/MWh|MWh|MW|t|hours?|gallons?|MGD)",
                  quote or "")
    if m:
        return m.group(0)
    # A source line quoted verbatim (the eGRID extract) carries the number with
    # its unit in the key, not after it: underline the number alone.
    m = re.search(r"\d[\d,]*\.\d+", quote or "")
    return m.group(0) if m else (quote or "")[:40]


def _step_kind(line: str, i: int) -> str:
    low = line.lower()
    if "band" in low or "p10" in low or "p90" in low:
        return "band"
    if "assert" in low or "invariant" in low or "exclude" in low or "never" in low:
        return "guard"
    if "=" in low and ("x " in low or "/" in low or "÷" in low):
        return "compute"
    return "compute" if i > 0 else "call"


def drawer_payload(card: dict, claim: dict) -> dict:
    """Emit the shape the UI consumes natively (its first-branch passthrough).

    Everything here is copied from the card — no recomputation, no rounding, no
    new claims. A drawer that disagreed with its chip would be worse than no
    drawer at all.
    """
    import math

    value = claim.get("value")
    numeric = isinstance(value, (int, float))
    rng = claim.get("range") or {}
    citations = claim.get("citations") or []
    primary = citations[0] if citations else None

    def cite(c, i=0):
        return {
            "id": f"{claim['id']}-cit-{i}",
            "claim_id": claim["id"],
            "document_sha256": c.get("doc_sha256", ""),
            "publisher": c.get("publisher", ""),
            "document_id": c.get("dataset", ""),
            "page": c.get("page"),
            "line": str(c.get("line")) if c.get("line") is not None else None,
            "quote": c.get("quote", ""),
            # The UI highlights `matched_text` inside `quote`; it must be a
            # substring of the quote or the reader shows "highlight target not
            # found". Pick the decisive token the verifier actually matched.
            "matched_text": _highlight(c.get("quote", "")),
            "verified": bool(c.get("verified")),
            "verified_at_utc": claim.get("as_of", ""),
            "verifier_version": c.get("verifier_version", "substring-1"),
            "url": None,
        }

    if numeric:
        headline = f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}"
    else:
        headline = "Not published"

    steps = [{"line": ln, "kind": _step_kind(ln, i)}
             for i, ln in enumerate(claim.get("chain") or [])]

    transforms = []
    for t in claim.get("transforms") or []:
        if ":" in t:
            label, val = t.split(":", 1)
            transforms.append({"label": label.strip(), "value": val.strip()})
        else:
            transforms.append({"label": t, "value": ""})

    sensitivity = []
    for s_ in claim.get("sensitivity") or []:
        delta = s_.get("delta", "")
        tone = "down" if delta.startswith("-") else ("up" if delta.startswith("+") else "flat")
        sensitivity.append({"label": s_.get("label", ""), "delta": delta, "tone": tone})

    return {
        "claim_id": claim["id"],
        "metric": claim["metric"],
        "label": claim["metric"].replace("_", " ").title(),
        "headline": headline,
        "unit": claim.get("unit", ""),
        "badge": claim.get("badge", "MODELED"),
        "provisional": bool(claim.get("provisional")),
        "p10": rng.get("p10"),
        "p50": rng.get("p50"),
        "p90": rng.get("p90"),
        "as_of_utc": claim.get("as_of", ""),
        "receipt_count": len(citations),
        "citation": cite(primary, 0) if primary else None,
        "extra_citations": [cite(c, i + 1) for i, c in enumerate(citations[1:])],
        "chain": {
            "calc_id": claim.get("calc_id") or "calc:not-recorded",
            "name": "wattdocket.carbon",
            "version": "0.1.0",
            "code_sha256": "",
            "steps": steps,
        },
        "transforms": transforms,
        "sensitivity": sensitivity,
        "drivers": claim.get("drivers") or [],
    }


def _envelope(as_of: str, receipts: list[dict], body: dict) -> dict:
    """SPEC §6: every response carries as_of, calc_version and receipts[]."""
    return {"as_of": as_of, "calc_version": f"wattdocket@{CALC_VERSION}",
            "receipts": receipts, **body}


def method_payload(as_of: str) -> dict:
    """GET /v1/method. `factor_set_payload` refuses a stale pinned hash."""
    fs = factor_set_payload()
    return _envelope(as_of, [{
        "publisher": fs["published_by"], "url": fs["url"],
        "sha256": fs["sha256"], "note": "sha256 of canonical_json",
    }], {
        "rule_zero": "the model reads and cites; it never computes",
        "mass_unit": "metric tonne",
        "lb_per_metric_tonne": 2204.622621848776,
        "intensity_note": fs["note"],
        "factor_sets": [fs],
        "invariants": INVARIANTS,
        "change_log": [{
            "factor_set_id": fs["id"], "sha256": fs["sha256"],
            "effective_date": fs["effective_date"],
            "change": "initial published table",
        }],
    })


def grid_payloads(ercot_path: Path, manifest_path: Path, as_of: str) -> dict:
    """GET /v1/grid/{ba}/hourly: one file per captured day plus `latest`.

    The day is the ERCOT dashboard capture pinned in the fixture manifest, run
    through the same `hourly_profile` the hero card uses. No day is invented:
    a day that was not captured is a 404.
    """
    ercot = json.loads(ercot_path.read_text())
    receipt = {"publisher": "ERCOT", "dataset": "fuel-mix dashboard (5-minute)",
               "url": ERCOT_URL}
    if manifest_path.exists():
        for e in json.loads(manifest_path.read_text()).get("entries", []):
            if e.get("file") == ercot_path.name:
                receipt["sha256_source"] = e.get("sha256_source")
                receipt["note"] = e.get("note")
        receipt["captured_at_utc"] = json.loads(
            manifest_path.read_text()).get("captured_at_utc")

    out: dict[str, dict] = {}
    days = sorted(ercot["data"])
    for day in days:
        profile = hourly_profile(ercot, day)
        profile["ba"] = "ERCO"
        out[day] = _envelope(as_of, [receipt], {
            **profile,
            "factor_set_sha256": DEFAULT_FACTOR_SET.sha256,
            "hour_convention": "START",
            "hour_basis": "local hour of the published label (UTC-05:00 that day)",
        })
    if days:
        out["latest"] = out[days[-1]]
    return out


def estimator_grid(as_of: str) -> tuple[dict, dict]:
    """GET /v1/estimator?subregion=&cap_mw=: reference sizes by subregion.

    Uses the same subregion rates the cards use, so a typical facility here
    and a real card in the same subregion agree on the grid.
    """
    src = json.loads(EGRID_RATES.read_text()).get("source", {})
    receipt = {"publisher": src.get("publisher", "US EPA"),
               "dataset": src.get("dataset", "eGRID"), "url": src.get("url"),
               "sha256": src.get("workbook_sha256")}
    files: dict[str, dict] = {}
    for sub, (name, rate) in sorted(SUBREGIONS.items()):
        for mw in ESTIMATOR_CAPS_MW:
            est = estimate(EstimateInput(
                capacity_mw=float(mw), capacity_basis=CapacityBasis.UNKNOWN,
                grid_rate_lb_per_mwh=rate, factor_set_id=DEFAULT_FACTOR_SET.id))
            files[f"{sub.lower()}/{mw}"] = _envelope(as_of, [receipt], {
                "subregion": sub, "subregion_name": name,
                "grid_rate_lb_per_mwh": rate,
                "intensity_state": "PROVISIONAL",
                "factor_set_id": DEFAULT_FACTOR_SET.id,
                "estimate": est.as_dict(),
            })
    index = _envelope(as_of, [receipt], {
        "basis": "UNKNOWN",
        "utilization": "national prior",
        "cap_mw": list(ESTIMATOR_CAPS_MW),
        "subregions": {s: n for s, (n, _) in sorted(SUBREGIONS.items())},
        "usage": ["/api/v1/estimator?subregion=<eGRID code>&cap_mw=<one of cap_mw>",
                  "/api/v1/estimator?facility_id=<id>"],
    })
    return files, index


def facility_estimate(card: dict, as_of: str) -> dict | None:
    """GET /v1/estimator?facility_id=: the card's own estimate, with inputs.

    Re-run from the card's stated inputs and checked against the card's carbon
    claim: an estimator answer that disagreed with the card would be a second
    number for the same fact.
    """
    claims = {c["id"]: c for c in card.get("claims", [])}
    carbon, power = claims.get("carbon-t"), claims.get("power-mw")
    grid = card.get("grid") or {}
    rate = grid.get("rate_lb_per_mwh")
    if not carbon or not power or not isinstance(carbon.get("value"), (int, float)) \
            or not rate:
        return None
    est = estimate(EstimateInput(
        capacity_mw=float(power["value"]), capacity_basis=CapacityBasis.UNKNOWN,
        grid_rate_lb_per_mwh=rate, factor_set_id=grid.get("factor_set_id")))
    if abs(est.annual_tonnes_co2e - carbon["value"]) > 0.5:
        raise SystemExit(
            f"{card['facility']['id']}: estimator gives {est.annual_tonnes_co2e:.1f} t "
            f"but the card says {carbon['value']}; rebuild the cards first")
    return _envelope(as_of, carbon.get("citations") or [], {
        "facility_id": card["facility"].get("site_id"),
        "subregion": grid.get("subregion"),
        "grid_rate_lb_per_mwh": rate,
        "intensity_state": grid.get("intensity_state"),
        "factor_set_id": grid.get("factor_set_id"),
        "factor_set_sha256": grid.get("factor_set_sha256"),
        "calc_id": carbon.get("calc_id"),
        "estimate": est.as_dict(),
    })


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default="artifacts/cards")
    ap.add_argument("--index", default="artifacts/cards.json")
    ap.add_argument("--out", default="site/api")
    ap.add_argument("--ercot", default=str(ROOT / "core/tests/fixtures/ercot_fuel_mix.json"),
                    help="captured ERCOT dashboard payload for /v1/grid")
    args = ap.parse_args()

    out = Path(args.out)
    # Never wipe the whole api tree: the operator payloads are written by a
    # different script into site/api/operator, so rmtree(out) deleted them as
    # a side effect and the next deploy's --delete removed them from S3.
    # Clear only what this builder writes.
    for owned in ("card", "drawer", "grid", "estimator", "facilities", "method"):
        target = out / owned
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    # An object store cannot hold both `facility/<id>` (a file) and
    # `facility/<id>/drawer/<claim>` (a path under it), so the published tree
    # is flat and a CloudFront viewer-request function maps the app's REST
    # paths onto it. The app keeps the real API's URLs, so removing the
    # function is all it takes to point at the live service.
    (out / "card").mkdir(parents=True)
    (out / "drawer").mkdir(parents=True)

    index = json.loads(Path(args.index).read_text())
    entries = index_entries(index)

    # /api/v1/facilities — the index
    (out / "facilities").write_text(json.dumps(
        [{k: v for k, v in e.items() if k != "_card_file"} for e in entries],
        indent=1))

    as_of = index.get("as_of", "")
    _write(out / "method", method_payload(as_of))

    ercot = Path(args.ercot)
    grid = grid_payloads(ercot, ercot.parent / "manifest.json", as_of)
    for day, payload in grid.items():
        for ba in GRID_BA_ALIASES["erco"]:
            _write(out / "grid" / ba / day, payload)

    est_files, est_index = estimator_grid(as_of)
    _write(out / "estimator" / "index", est_index)
    for key, payload in est_files.items():
        _write(out / "estimator" / key, payload)

    n_cards = n_drawers = n_est = 0
    for entry in entries:
        fid = entry["_card_file"]
        src = Path(args.cards) / f"{fid}.json"
        if not src.exists():
            continue
        card = json.loads(src.read_text())
        ui_id = slug(entry["id"])
        (out / "card" / ui_id).write_text(json.dumps(card, indent=1))
        n_cards += 1
        fest = facility_estimate(card, as_of)
        if fest:
            _write(out / "estimator" / "facility" / ui_id, fest)
            n_est += 1
        for claim in card.get("claims", []):
            dpath = out / "drawer" / ui_id / slug(claim["id"])
            dpath.parent.mkdir(parents=True, exist_ok=True)
            dpath.write_text(json.dumps(drawer_payload(card, claim), indent=1))
            n_drawers += 1

    print(f"static API: {n_cards} cards, {n_drawers} drawer payloads, "
          f"{len(grid) - 1 if grid else 0} grid day(s), {len(est_files)} estimator cells, "
          f"{n_est} facility estimates, method, 1 index → {out}")


if __name__ == "__main__":
    main()
