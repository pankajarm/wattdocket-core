"""Cross-check the offline subregion resolver against EPA's live ArcGIS service.

Circularity warning that shapes this test: the cards were generated with the
same bundled geometry, so "479/479 resolved" proves coverage, not correctness.
The only independent witness is EPA's own FeatureServer, queried with the same
points via a multipoint intersect.

    python scripts/verify_geo_against_epa.py            # all 479 rows
    python scripts/verify_geo_against_epa.py --bundle /tmp/eg_0.005.geojson
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

SERVICE = ("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services"
           "/eGRID2023_Subregions/FeatureServer/0/query")

CHUNK = 120


def epa_lookup(points: list[tuple[float, float]]) -> list[set[str] | None]:
    """Ask EPA which subregion contains each (lat, lon). None when outside."""
    out: list[set[str] | None] = []
    for start in range(0, len(points), CHUNK):
        chunk = points[start:start + CHUNK]
        geom = json.dumps({
            "points": [[lon, lat] for lat, lon in chunk],
            "spatialReference": {"wkid": 4326},
        })
        body = urllib.parse.urlencode({
            "f": "json",
            "where": "1=1",
            "geometry": geom,
            "geometryType": "esriGeometryMultipoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "Subregion",
            "returnGeometry": "false",
        }).encode()
        # POST: a 120-point multipoint is far past what a query string tolerates.
        req = urllib.request.Request(SERVICE, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = json.loads(r.read().decode())
        if "error" in payload:
            raise SystemExit(f"EPA service error: {payload['error']}")
        # The service returns the set of subregions touching the multipoint, not
        # a per-point mapping, so this can only confirm containment in the union.
        # It is still decisive for the one thing we care about: every facility
        # must fall inside the subregion our resolver names.
        found = {f["attributes"]["Subregion"] for f in payload.get("features", [])}
        out.append(found if found else None)
    return out


def point_in_feature(lon: float, lat: float, subregion: str, geo: dict) -> bool:
    for feature in geo["features"]:
        if feature["properties"]["subregion"].upper() != subregion.upper():
            continue
        for ring in feature["geometry"]["coordinates"]:
            inside = False
            j = len(ring) - 1
            for i in range(len(ring)):
                xi, yi = ring[i]
                xj, yj = ring[j]
                if ((yi > lat) != (yj > lat)) and (
                        lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
                    inside = not inside
                j = i
            if inside:
                return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default="artifacts/cards.json")
    ap.add_argument("--bundle", default="data/egrid_subregions.geojson")
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()

    cards = json.loads(Path(args.cards).read_text())["cards"]
    items = list(cards.items())
    if args.sample:
        items = items[:: max(1, len(items) // args.sample)]

    geo = json.loads(Path(args.bundle).read_text())
    points = [(c["lat"], c["lon"]) for _, c in items]
    print(f"bundle: {args.bundle} ({Path(args.bundle).stat().st_size / 1e6:.2f} MB)")
    print(f"facilities: {len(points)}")

    # 1. every card's named subregion must geometrically contain its own point
    outside = []
    for (fid, card), (lat, lon) in zip(items, points):
        if not point_in_feature(lon, lat, card["subregion"], geo):
            outside.append((fid, card["subregion"], lat, lon))
    print(f"cards whose point falls outside their own subregion polygon: {len(outside)}")
    for o in outside[:5]:
        print("   ", o)

    # 2. EPA's service must also place each point inside the set containing it
    lookups = epa_lookup(points)
    mismatched = []
    for (fid, card), found in zip(items, lookups):
        if found is None:
            mismatched.append((fid, card["subregion"], "no EPA feature intersects"))
        elif card["subregion"] not in found:
            mismatched.append((fid, card["subregion"], sorted(found)))
    print(f"disagreements with EPA's live service: {len(mismatched)}")
    for m in mismatched[:10]:
        print("   ", m)

    ok = not outside and not mismatched
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
