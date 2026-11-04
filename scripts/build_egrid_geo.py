"""Compress the EPA eGRID subregion polygons into a bundle we can ship.

The published ArcGIS layer is 146 MB of full-precision rings — far too large to
bake into a Lambda image or a static site. Subregions are large, simple shapes
at the scale we care about (which utility territory is this address in?), so a
Douglas-Peucker pass at ~0.01 degrees keeps every boundary decision honest while
cutting the payload by two orders of magnitude.

    python scripts/build_egrid_geo.py --raw /tmp/egrid_geo_raw.json \
        --out data/egrid_subregions.geojson
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TOLERANCE = 0.01      # degrees, ~1.1 km — far below any subregion boundary width
QUANTIZE = 3          # decimal places: 0.001 deg ~ 110 m, plenty for a territory
MIN_POINTS = 4


def _perp(p, a, b) -> float:
    """Perpendicular distance from p to segment ab (all 2-tuples)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def simplify(points: list[list[float]], tol: float) -> list[list[float]]:
    """Iterative Douglas-Peucker — recursion depth is a real risk at this size."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        dmax, index = 0.0, start
        for i in range(start + 1, end):
            d = _perp(points[i], points[start], points[end])
            if d > dmax:
                dmax, index = d, i
        if dmax > tol:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    out = [p for p, k in zip(points, keep) if k]
    return out if len(out) >= MIN_POINTS else points


def ring_area(ring: list[list[float]]) -> float:
    """Shoelace — used to drop slivers created by simplification."""
    s = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="/tmp/egrid_geo_raw.json")
    ap.add_argument("--out", default="data/egrid_subregions.geojson")
    ap.add_argument("--tolerance", type=float, default=TOLERANCE)
    args = ap.parse_args()

    raw = json.loads(Path(args.raw).read_text())
    features = raw.get("features") or []
    if not features:
        raise SystemExit("no features in the source file")

    out_features = []
    kept_points = total_points = 0
    for f in features:
        sub = (f.get("attributes") or {}).get("Subregion")
        rings = (f.get("geometry") or {}).get("rings") or []
        if not sub or not rings:
            continue
        new_rings = []
        for ring in rings:
            total_points += len(ring)
            simple = simplify(ring, args.tolerance)
            if len(simple) < 4:
                continue
            # close the ring if simplification opened it
            if simple[0] != simple[-1]:
                simple = simple + [simple[0]]
            if ring_area(simple) < 1e-6:      # a sliver, not a boundary
                continue
            q = [[round(x, QUANTIZE), round(y, QUANTIZE)] for x, y in simple]
            deduped = [q[0]]
            for pt in q[1:]:
                if pt != deduped[-1]:
                    deduped.append(pt)
            if len(deduped) < 4:
                continue
            new_rings.append(deduped)
            kept_points += len(deduped)
        if new_rings:
            out_features.append({
                "type": "Feature",
                "properties": {"subregion": sub},
                "geometry": {"type": "Polygon", "coordinates": new_rings},
            })

    out = {"type": "FeatureCollection", "features": out_features}
    text = json.dumps(out, separators=(",", ":"))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)

    print(f"subregions: {len(out_features)}")
    print(f"points: {total_points:,} -> {kept_points:,} "
          f"({kept_points / total_points:.1%} kept)")
    print(f"size: {len(text) / 1e6:.2f} MB -> {args.out}")


if __name__ == "__main__":
    main()
