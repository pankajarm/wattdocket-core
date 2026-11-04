"""The public API — FastAPI, deterministic first.

Every route except `/v1/explain` is pure computation over the ledger. The
model is reachable from exactly one place, it has a timeout and a circuit
breaker, and when it fails this service returns a static tooltip rather than
an error. That is what makes `MODEL_OFF=1` a real guarantee and not a hope.

Run locally:
    uvicorn wattdocket.api.main:app --reload --port 8080
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from wattdocket.estimator import CapacityBasis, EstimateInput, estimate
from wattdocket.grid import IntensityState

LEDGER_PATH = Path(os.environ.get("WATTDOCKET_LEDGER", "ledger.sqlite"))
ARTIFACT_DIR = Path(os.environ.get("WATTDOCKET_ARTIFACTS", "artifacts"))
MODEL_ENABLED = os.environ.get("MODEL_OFF", "0") not in ("1", "true", "yes")


# --------------------------------------------------------------------------
# data access
# --------------------------------------------------------------------------
class Ledger:
    """Thin read-only wrapper. Falls back to fixtures when the ledger is absent."""

    def __init__(self) -> None:
        self._conn = None
        self._fixtures = None
        if LEDGER_PATH.exists():
            import sqlite3
            self._conn = sqlite3.connect(f"file:{LEDGER_PATH}?mode=ro", uri=True)
            self._conn.row_factory = sqlite3.Row
        else:
            fx = ARTIFACT_DIR / "fixtures.json"
            if fx.exists():
                self._fixtures = json.loads(fx.read_text())

    @property
    def mode(self) -> str:
        if self._conn:
            return "ledger"
        if self._fixtures:
            return "fixtures"
        return "empty"

    def facilities(self) -> list[dict]:
        if self._conn:
            rows = self._conn.execute(
                "select * from facility order by name").fetchall()
            return [dict(r) for r in rows]
        if self._fixtures:
            return self._fixtures.get("facilities", [])
        return []

    def facility(self, fid: str) -> dict | None:
        if self._conn:
            r = self._conn.execute(
                "select * from facility where id = ?", (fid,)).fetchone()
            return dict(r) if r else None
        for f in self.facilities():
            if f.get("id") == fid:
                return f
        return None

    def card(self, fid: str) -> dict | None:
        p = ARTIFACT_DIR / "cards" / f"{fid}.json"
        if p.exists():
            return json.loads(p.read_text())
        if ARTIFACT_DIR.exists():
            p2 = ARTIFACT_DIR / "cards.json"
            if p2.exists():
                all_cards = json.loads(p2.read_text())
                return all_cards.get("cards", {}).get(fid)
        if self._fixtures:
            return self._fixtures.get("cards", {}).get(fid)
        return None

    def hourly(self, ba: str, day: str) -> dict | None:
        p = ARTIFACT_DIR / "hourly" / f"{ba}_{day}.json"
        if p.exists():
            return json.loads(p.read_text())
        if self._fixtures:
            return (self._fixtures.get("hourly") or {}).get(f"{ba}:{day}")
        return None


ledger = Ledger()


# --------------------------------------------------------------------------
# app
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[wattdocket] ledger mode={ledger.mode} model_enabled={MODEL_ENABLED}")
    yield


app = FastAPI(title="WattDocket API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("WATTDOCKET_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "ledger": ledger.mode, "model_enabled": MODEL_ENABLED,
            "version": app.version}


@app.get("/v1/facilities")
def facilities(q: str = Query("", max_length=120), limit: int = 50) -> dict:
    items = ledger.facilities()
    if q:
        needle = q.lower()
        items = [f for f in items
                 if needle in (f.get("name") or "").lower()
                 or needle in (f.get("city") or "").lower()
                 or needle in (f.get("address") or "").lower()]
    return {"count": len(items), "results": items[:limit],
            "truncated": len(items) > limit}


@app.get("/v1/facility/{facility_id}")
def facility(facility_id: str) -> dict:
    card = ledger.card(facility_id)
    if card:
        return card
    f = ledger.facility(facility_id)
    if not f:
        raise HTTPException(404, f"unknown facility {facility_id}")
    # No precomputed card: return the facility shell and say so, rather than
    # inventing numbers. The UI renders the "not yet modelled" state.
    return {"facility": f, "claims": [], "status": "no_card_precomputed"}


@app.get("/v1/facility/{facility_id}/drawer/{claim_id}")
def drawer(facility_id: str, claim_id: str) -> dict:
    card = ledger.card(facility_id) or {}
    for c in card.get("claims", []):
        if c.get("id") == claim_id:
            return {"claim": c, "receipts": c.get("citations", []),
                    "chain": c.get("chain", []),
                    "transforms": c.get("transforms", []),
                    "sensitivity": c.get("sensitivity", [])}
    raise HTTPException(404, "unknown claim")


@app.get("/v1/grid/{ba}/hourly")
def hourly(ba: str, day: str) -> dict:
    payload = ledger.hourly(ba.upper(), day)
    if not payload:
        raise HTTPException(404, f"no hourly series for {ba} on {day}")
    return payload


@app.get("/v1/estimator")
def estimator(cap_mw: float = Query(..., gt=0),
              basis: CapacityBasis = CapacityBasis.UNKNOWN,
              util: float | None = Query(None, gt=0, le=1),
              pue: float | None = Query(None, ge=1.0),
              rate: float = Query(..., gt=0, description="lb CO2e/MWh"),
              state: IntensityState = IntensityState.PROVISIONAL,
              factor_set: str = "us-avg-2023.1") -> dict:
    """Pure computation. No ledger, no model, no I/O."""
    try:
        est = estimate(EstimateInput(
            capacity_mw=cap_mw, capacity_basis=basis, utilization=util,
            pue=pue, grid_rate_lb_per_mwh=rate))
    except Exception as e:                      # noqa: BLE001 - surfaced to client
        raise HTTPException(422, str(e)) from e
    out = est.as_dict()
    out["intensity_state"] = state.value
    out["factor_set_id"] = factor_set
    return out


@app.post("/v1/explain")
def explain(body: dict) -> dict:
    """The only model route. Degrades to a template, never errors."""
    text = (body.get("template") or "").strip()
    payload = body.get("payload") or {}
    if not MODEL_ENABLED:
        return {"text": text, "source": "template", "model": None,
                "degraded": True, "reason": "MODEL_OFF"}
    try:
        from wattdocket.explain import compose
        prose = compose(payload)
        return {"text": prose, "source": "model", "model": "muse-spark-1.3",
                "degraded": False}
    except Exception as e:                      # noqa: BLE001
        return {"text": text, "source": "template", "model": None,
                "degraded": True, "reason": type(e).__name__}


@app.get("/v1/method")
def method() -> dict:
    p = ARTIFACT_DIR / "method.json"
    if p.exists():
        return json.loads(p.read_text())
    return {
        "rule_zero": "the model reads and cites; it never computes",
        "mass_unit": "metric tonne",
        "lb_per_metric_tonne": 2204.6226218487757,
        "invariants": [
            "INV-1 metric tonnes", "INV-2 UTC storage, DST-safe",
            "INV-3 explicit hour convention", "INV-4 PUE only for IT basis",
            "INV-5 storage excluded", "INV-6 provisional carries its factor set",
            "INV-7 no claim renders without a verified citation",
            "INV-8 empty is not zero", "INV-9 water is never a scalar",
            "INV-10 model prose may only restate computed values",
        ],
    }
