"""Address (or facility coordinate) -> eGRID subregion -> :class:`GridBinding`.

Responsible for: the SPEC §2 ``GridBinding`` row, and for the rule that a
binding names how it was made. Only ``point_in_polygon`` is implemented here;
``operator_filing`` and ``manual`` exist in the schema for the curated layer.

Two keyless services, in a fixed order:

1. **US Census geocoder** (primary — authoritative address ranges, no key):
   ``/geocoder/locations/onelineaddress``.
2. **Nominatim** (fallback — community data, needs a User-Agent, rate limited):
   ``/search``.

Then a point-in-polygon test against EPA's eGRID 2023 subregion feature
service. Three outcomes are kept distinct, because they are different facts:

* a subregion code — the point is inside a known polygon;
* :class:`SubregionNotFound` — the service answered and the point is in no
  polygon (offshore, or a territory eGRID does not cover);
* :class:`IngestionError` — the service did not answer. That must stop a build;
  an outage is never "no subregion".
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .net import Fetcher, IngestionError, default_fetcher, sha256_bytes, canonical_json

CENSUS_BASE = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CENSUS_BENCHMARK = "Public_AR_Current"
NOMINATIM_BASE = "https://nominatim.openstreetmap.org/search"
ARCGIS_BASE = ("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
               "eGRID2023_Subregions/FeatureServer/0/query")
ARCGIS_SUBREGION_URL = ("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/"
                        "services/eGRID2023_Subregions/FeatureServer/0")
# The only subregion->balancing-authority pairing we pin, because ERCOT's BA
# code is not an inference: ERCT *is* ERCOT.
KNOWN_BA = {"ERCT": "ERCO"}


class GeocodeError(IngestionError):
    pass


class SubregionNotFound(GeocodeError):
    """The polygon service answered, and the point is in no subregion."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def census_url(address: str) -> str:
    query = urllib.parse.urlencode(
        {"address": address, "benchmark": CENSUS_BENCHMARK, "format": "json"},
        quote_via=urllib.parse.quote)
    return f"{CENSUS_BASE}?{query}"


def nominatim_url(address: str) -> str:
    query = urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": "1"},
        quote_via=urllib.parse.quote)
    return f"{NOMINATIM_BASE}?{query}"


def polygon_url(lat: float, lon: float) -> str:
    query = urllib.parse.urlencode({
        "geometry": f"{lon:.6f},{lat:.6f}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "Subregion",
        "returnGeometry": "false",
        "f": "json",
    }, quote_via=urllib.parse.quote)
    return f"{ARCGIS_BASE}?{query}"


def census_geocode(address: str, fetcher: Fetcher | None = None) -> dict:
    fetcher = fetcher or default_fetcher()
    url = census_url(address)
    payload = fetcher.get_json(url)
    try:
        matches = payload["result"]["addressMatches"]
    except (KeyError, TypeError) as exc:
        raise GeocodeError(
            f"Census geocoder response has no result.addressMatches "
            f"(keys={list(payload) if isinstance(payload, dict) else type(payload)})",
            url) from exc
    if not matches:
        raise GeocodeError(f"Census geocoder found no match for {address!r}", url)
    best = matches[0]
    coordinates = best.get("coordinates") or {}
    lat, lon = coordinates.get("y"), coordinates.get("x")
    if lat is None or lon is None:
        raise GeocodeError(f"Census match for {address!r} has no coordinates", url)
    return {
        "lat": float(lat),
        "lon": float(lon),
        "matched_address": best.get("matchedAddress"),
        "geocoder": "census",
        "geocoder_url": url,
    }


def nominatim_geocode(address: str, fetcher: Fetcher | None = None) -> dict:
    fetcher = fetcher or default_fetcher()
    url = nominatim_url(address)
    payload = fetcher.get_json(url)
    if not isinstance(payload, list) or not payload:
        raise GeocodeError(f"Nominatim found no match for {address!r}", url)
    best = payload[0]
    try:
        lat, lon = float(best["lat"]), float(best["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeocodeError(f"Nominatim match for {address!r} is malformed", url) from exc
    return {
        "lat": lat,
        "lon": lon,
        "matched_address": best.get("display_name"),
        "geocoder": "nominatim",
        "geocoder_url": url,
    }


def geocode(address: str, fetcher: Fetcher | None = None) -> dict:
    """Census first, Nominatim second; both failures are reported together."""
    if not address or not address.strip():
        raise GeocodeError("empty address")
    fetcher = fetcher or default_fetcher()
    try:
        return census_geocode(address, fetcher)
    except IngestionError as primary:
        try:
            return nominatim_geocode(address, fetcher)
        except IngestionError as secondary:
            raise GeocodeError(
                f"both geocoders failed for {address!r}: census -> {primary}; "
                f"nominatim -> {secondary}") from secondary


def subregion_for_point(lat: float, lon: float, fetcher: Fetcher | None = None) -> str:
    """Point-in-polygon against EPA's eGRID 2023 subregion service."""
    fetcher = fetcher or default_fetcher()
    url = polygon_url(lat, lon)
    payload: Any = fetcher.get_json(url)
    if not isinstance(payload, dict):
        raise GeocodeError("eGRID polygon service returned a non-object", url)
    if "error" in payload:
        raise GeocodeError(f"eGRID polygon service error: {payload['error']}", url)
    features = payload.get("features")
    if features is None:
        raise GeocodeError(
            f"eGRID polygon response has no 'features' array (keys={list(payload)})", url)
    if not features:
        raise SubregionNotFound(
            f"point {lat:.5f},{lon:.5f} falls in no eGRID 2023 subregion", url)
    codes = sorted({str(f.get("attributes", {}).get("Subregion") or "").strip().upper()
                    for f in features})
    codes = [c for c in codes if c]
    if not codes:
        raise GeocodeError("eGRID polygon response has features but no Subregion "
                           "attribute", url)
    if len(codes) > 1:
        raise GeocodeError(f"point {lat:.5f},{lon:.5f} intersects several subregions "
                           f"{codes}; overlapping polygons are a data error", url)
    return codes[0]


def binding_for_facility(facility: dict, fetcher: Fetcher | None = None,
                         resolved_at: str | None = None,
                         allow_unresolved: bool = True) -> dict:
    """Facility coordinates -> ledger ``grid_binding`` row.

    ``allow_unresolved`` keeps a point that no polygon covers as an explicit
    ``resolved=0`` row rather than dropping the facility. Transport failures
    always raise.
    """
    fetcher = fetcher or default_fetcher()
    lat, lon = facility["lat"], facility["lon"]
    stamp = resolved_at or _now_utc()
    try:
        subregion = subregion_for_point(lat, lon, fetcher)
        resolved = 1
        note = None
    except SubregionNotFound as exc:
        if not allow_unresolved:
            raise
        subregion, resolved, note = None, 0, str(exc)
    return {
        "facility_id": facility["id"],
        "utility": None,
        "balancing_authority": KNOWN_BA.get(subregion or ""),
        "egrind_subregion": subregion,
        "method": "point_in_polygon",
        "lat": lat,
        "lon": lon,
        "resolved": resolved,
        "note": note,
        "source_id": "epa-egrid-2023-subregion-polygons",
        "source_url": ARCGIS_SUBREGION_URL,
        "resolved_at_utc": stamp,
    }


def binding_for_address(address: str, fetcher: Fetcher | None = None,
                        facility_id: str | None = None,
                        resolved_at: str | None = None) -> dict:
    """Address -> ledger ``grid_binding`` row (geocode, then polygon)."""
    fetcher = fetcher or default_fetcher()
    located = geocode(address, fetcher)
    subregion = subregion_for_point(located["lat"], located["lon"], fetcher)
    stamp = resolved_at or _now_utc()
    return {
        "facility_id": facility_id or "address:" + located["matched_address"],
        "utility": None,
        "balancing_authority": KNOWN_BA.get(subregion),
        "egrind_subregion": subregion,
        "method": "point_in_polygon",
        "lat": located["lat"],
        "lon": located["lon"],
        "resolved": 1,
        "note": f"geocoder={located['geocoder']}; matched={located['matched_address']}",
        "source_id": "epa-egrid-2023-subregion-polygons",
        "source_url": ARCGIS_SUBREGION_URL,
        "resolved_at_utc": stamp,
    }


def geocode_digest(located: dict) -> str:
    """Reproducibility hook: the geocoder answer as a hash."""
    return sha256_bytes(canonical_json(located).encode("utf-8"))


# --------------------------------------------------------------------------
# Bundled eGRID polygons — point-in-polygon with no network at build time.
#
# The live service returns 146 MB of Esri JSON. We bundle it once, quantised,
# so ``cards.py`` resolves 479 rows in about a second with no dependency
# on ArcGIS being up. Quantising every ring to the same grid keeps shared
# borders identical between neighbouring subregions, so a facility cannot fall
# into a hairline gap created by simplifying one polygon but not its neighbour.
# --------------------------------------------------------------------------

BUNDLE_PATH = Path("data") / "egrid_subregions.geojson"
COORD_DECIMALS = 4          # ~11 m at US latitudes
MIN_RING_POINTS = 4         # a ring needs 3 distinct points + the closing point


def _douglas_peucker(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """Simplification that keeps the endpoints of every segment fixed.

    Endpoint anchoring is what protects shared borders: two subregions that
    share a border store the same vertex run, so simplifying it from either
    side keeps the same vertices and no hairline gap opens between them.
    """
    if tolerance <= 0 or len(points) <= 2:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        x1, y1 = points[first]
        x2, y2 = points[last]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        best_index, best_distance = -1, -1.0
        for index in range(first + 1, last):
            px, py = points[index]
            if norm == 0:
                distance = ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
            else:
                distance = abs(dy * (px - x1) - dx * (py - y1)) / norm
            if distance > best_distance:
                best_index, best_distance = index, distance
        if best_distance > tolerance and best_index > 0:
            keep[best_index] = True
            stack.append((first, best_index))
            stack.append((best_index, last))
    return [point for point, kept in zip(points, keep) if kept]


def _quantise(ring: Sequence[Sequence[float]], decimals: int = COORD_DECIMALS,
              tolerance: float = 0.0) -> list[list[float]]:
    """Round, drop repeated vertices, optionally simplify, then close the ring."""
    points: list[list[float]] = []
    for pair in ring:
        point = [round(float(pair[0]), decimals), round(float(pair[1]), decimals)]
        if not points or point != points[-1]:
            points.append(point)
    if len(points) > 1 and points[0] != points[-1]:
        points.append(list(points[0]))
    if tolerance > 0 and len(points) > 3:
        closed = points[0] == points[-1]
        body = _douglas_peucker(points[:-1] if closed else points, tolerance)
        points = body + [list(body[0])] if closed and len(body) > 2 else body
    # Remove points that lie exactly on the segment between their neighbours.
    simplified: list[list[float]] = []
    for point in points:
        if len(simplified) >= 2:
            a, b = simplified[-2], simplified[-1]
            cross = ((b[0] - a[0]) * (point[1] - a[1])
                     - (b[1] - a[1]) * (point[0] - a[0]))
            if abs(cross) < 1e-12:
                simplified[-1] = point
                continue
        simplified.append(point)
    if len(simplified) > 1 and simplified[0] == simplified[-1]:
        simplified = simplified[:-1]
    if simplified:
        simplified.append(list(simplified[0]))
    return simplified


def _rings_of(geometry: dict) -> list[list[list[float]]]:
    """Both Esri ``rings`` and GeoJSON ``coordinates`` -> a flat ring list."""
    if not isinstance(geometry, dict):
        return []
    if "rings" in geometry:
        return [ring for ring in geometry["rings"] if ring]
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [ring for ring in coords if ring]
    if kind == "MultiPolygon":
        return [ring for polygon in coords for ring in polygon if ring]
    return []


def _bbox(rings: Iterable[Sequence[Sequence[float]]]) -> list[float]:
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    return [min(xs), min(ys), max(xs), max(ys)]


def build_subregion_bundle(esri_payload: dict, source_url: str = ARCGIS_BASE,
                           decimals: int = COORD_DECIMALS,
                           tolerance: float = 0.0) -> dict:
    """Esri FeatureServer JSON -> a compact GeoJSON FeatureCollection.

    Each subregion becomes one feature whose ``coordinates`` is a list of rings
    tested with the even-odd rule, so holes stay holes without any orientation
    assumptions about what EPA's exporter wrote.
    """
    features = esri_payload.get("features")
    if not isinstance(features, list) or not features:
        raise GeocodeError("eGRID geometry download has no features", source_url)
    out: list[dict] = []
    dropped = 0
    for feature in features:
        attributes = feature.get("attributes") or {}
        code = str(attributes.get("Subregion") or "").strip().upper()
        if not code:
            raise GeocodeError("eGRID geometry feature has no Subregion attribute",
                               source_url)
        rings = [_quantise(ring, decimals, tolerance)
                 for ring in _rings_of(feature.get("geometry") or {})]
        rings = [ring for ring in rings if len(ring) >= MIN_RING_POINTS]
        if not rings:
            dropped += 1
            continue
        out.append({
            "type": "Feature",
            "properties": {"Subregion": code, "SRNAME": attributes.get("SRNAME")},
            "geometry": {"type": "MultiPolygon",
                         "coordinates": [[ring] for ring in rings]},
            "bbox": _bbox(rings),
        })
    if not out:
        raise GeocodeError("no usable eGRID polygons after quantisation", source_url)
    out.sort(key=lambda f: f["properties"]["Subregion"])
    return {
        "type": "FeatureCollection",
        "properties": {
            "source_url": ARCGIS_BASE,
            "source_service": "eGRID2023_Subregions/FeatureServer/0",
            "publisher": "US EPA",
            "coordinate_decimals": decimals,
            "simplification_tolerance_deg": tolerance,
            "subregion_count": len(out),
            "rings_dropped_degenerate": dropped,
            "note": "quantised for bundling; rings tested with the even-odd rule",
        },
        "features": out,
    }


def load_subregion_polygons(path: str | Path) -> list[dict]:
    """Read the bundled GeoJSON into ``[{subregion, rings, bbox}]``."""
    path = Path(path)
    if not path.is_file():
        raise GeocodeError(
            f"bundled eGRID polygons not found: {path} "
            f"(re-create with python -m wattdocket.ingest.geo --bundle)")
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features")
    if not features:
        raise GeocodeError(f"bundled polygons file has no features: {path}")
    polygons: list[dict] = []
    for feature in features:
        rings = [ring for ring in _rings_of(feature.get("geometry") or {})
                 if len(ring) >= MIN_RING_POINTS]
        if not rings:
            continue
        props = feature.get("properties") or {}
        code = props.get("Subregion") or props.get("subregion")
        if not code:
            continue
        polygons.append({
            "subregion": str(code).upper(),
            "name": props.get("SRNAME") or props.get("name"),
            "rings": rings,
            "bbox": _bbox(rings),
        })
    return polygons


def _point_in_ring(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    """Ray casting; a point exactly on an edge counts as inside."""
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index][0], ring[index][1]
        x2, y2 = ring[(index + 1) % count][0], ring[(index + 1) % count][1]
        # on-segment test first, so boundary ties resolve to "inside"
        if (min(x1, x2) <= lon <= max(x1, x2) and min(y1, y2) <= lat <= max(y1, y2)):
            cross = (x2 - x1) * (lat - y1) - (y2 - y1) * (lon - x1)
            if abs(cross) < 1e-12:
                return True
        if (y1 > lat) != (y2 > lat):
            x_at = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_at:
                inside = not inside
    return inside


def subregion_for_point_local(lat: float, lon: float,
                              polygons: list[dict]) -> str | None:
    """Even-odd point-in-polygon over the bundled rings. ``None`` = no match."""
    hits: set[str] = set()
    for polygon in polygons:
        minx, miny, maxx, maxy = polygon["bbox"]
        if not (minx <= lon <= maxx and miny <= lat <= maxy):
            continue
        enclosures = sum(1 for ring in polygon["rings"]
                         if _point_in_ring(lon, lat, ring))
        if enclosures % 2 == 1:
            hits.add(polygon["subregion"])
    if not hits:
        return None
    if len(hits) > 1:
        raise GeocodeError(
            f"point {lat:.5f},{lon:.5f} is inside several bundled subregions "
            f"{sorted(hits)}; overlapping polygons are a data error")
    return hits.pop()


def write_subregion_bundle(out_path: str | Path, esri_payload: dict,
                           source_url: str = ARCGIS_BASE,
                           tolerance: float = 0.0,
                           decimals: int = COORD_DECIMALS) -> dict:
    """Bundle -> disk, deterministic bytes (sorted keys, no wall clock)."""
    bundle = build_subregion_bundle(esri_payload, source_url=source_url,
                                    decimals=decimals, tolerance=tolerance)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    out_path.write_text(text, encoding="utf-8")
    return {
        "path": str(out_path),
        "subregions": bundle["properties"]["subregion_count"],
        "bytes": len(text.encode("utf-8")),
        "sha256": sha256_bytes(text.encode("utf-8")),
    }


def main(argv: list[str] | None = None) -> int:
    """One-shot bundling of EPA's eGRID polygons (the only network step)."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="wattdocket.ingest.geo",
        description="Bundle EPA eGRID 2023 subregion polygons for offline use")
    parser.add_argument("--bundle", action="store_true", required=True,
                        help="download the feature service and write the bundle")
    parser.add_argument("--out", default=str(BUNDLE_PATH),
                        help="destination GeoJSON (default: data/egrid_subregions.geojson)")
    parser.add_argument("--from-file", default=None,
                        help="use an already-downloaded Esri JSON payload")
    parser.add_argument("--tolerance", type=float, default=0.0005,
                        help="Douglas-Peucker tolerance in degrees (default 0.0005 "
                             "≈ 55 m); 0 keeps every vertex")
    args = parser.parse_args(argv)

    if args.from_file:
        payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    else:
        raw = default_fetcher().get_bytes(
            f"{ARCGIS_BASE}?where=1%3D1&outFields=Subregion&returnGeometry=true"
            f"&outSR=4326&f=json")
        payload = json.loads(raw)
    print(json.dumps(write_subregion_bundle(args.out, payload,
                                            tolerance=args.tolerance), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
