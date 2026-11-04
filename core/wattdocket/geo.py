"""Address -> eGRID subregion, offline, by point-in-polygon.

Everything else in the product depends on this lookup: the subregion decides the
emission rate, and the rate decides every carbon number on the card. Getting it
from a state-level default was a placeholder — West Virginia and Virginia share
a default but not a grid, and Texas has three subregions inside one state.

The geometry is EPA's own eGRID2023 subregion layer, simplified and quantised by
`scripts/build_egrid_geo.py` (146 MB -> 4.2 MB, 110 m resolution). It ships with
the code so the lookup never touches the network.

    from wattdocket.geo import subregion_for_point
    subregion_for_point(29.48, -98.70)   # -> "ERCT"
"""

from __future__ import annotations

import json
import math
from pathlib import Path

_GEO_CANDIDATES = (
    Path(__file__).parent.parent.parent / "data" / "egrid_subregions.geojson",
    Path("data/egrid_subregions.geojson"),
    Path("../data/egrid_subregions.geojson"),
)

_INDEX: list[tuple[str, float, float, float, float, list[list[list[float]]]]] | None = None


def _load() -> list[tuple[str, float, float, float, float, list[list[list[float]]]]]:
    """Load once, into (subregion, minx, miny, maxx, maxy, rings) tuples."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX

    path = next((p for p in _GEO_CANDIDATES if p.exists()), None)
    if path is None:
        _INDEX = []
        return _INDEX

    data = json.loads(path.read_text())
    index = []
    for feature in data.get("features", []):
        sub = (feature.get("properties") or {}).get("subregion")
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if not sub or not coords:
            continue
        # A subregion is a single Polygon whose coordinate list may hold many
        # rings — Alaska alone has 147 island rings. Bounding only the first
        # ring crops the shape to one island and silently loses every mainland
        # point, so the bbox must span them all. Rings are treated as additive
        # (a hole is usually water, and a lake still sits inside a subregion):
        # better slightly generous than silently uncovered.
        rings = [r for r in coords if len(r) >= 4]
        if not rings:
            continue
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        index.append((sub, min(xs), min(ys), max(xs), max(ys), rings))
    _INDEX = index
    return _INDEX


def _point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    """Ray casting. Returns True when the point is inside the ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def subregion_for_point(lat: float, lon: float) -> str | None:
    """The eGRID subregion containing a point, or None when outside coverage."""
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    for sub, minx, miny, maxx, maxy, rings in _load():
        if not (minx <= lon <= maxx and miny <= lat <= maxy):
            continue
        for ring in rings:
            if _point_in_ring(lon, lat, ring):
                return sub
    return None


def coverage() -> dict:
    """Small diagnostic used by /healthz and the card builder."""
    idx = _load()
    return {
        "subregions": len(idx),
        "geometry_loaded": bool(idx),
        "source": "EPA eGRID2023 subregion polygons, simplified to 110 m",
    }
