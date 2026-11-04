"""Public grid feeds with explicit vintage, units, nulls and observation clocks.

These adapters perform no writes and never replace missing readings with zero.
The orchestrator owns raw-response archival, retries and last-good releases.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from ..ingest.egrid import parse_subregion_rates
from ..timeutil import parse_offset_iso
from .models import Fetcher, SourceResult

EGRID_DISCOVERY_URL = "https://www.epa.gov/egrid/detailed-data"
EIA_BASE_URL = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/"
EIA_MANIFEST_URL = EIA_BASE_URL + "EIA930_File_List_Meta.csv"
EIA_RESPONDENTS_URL = "https://www.eia.gov/electricity/930-api/respondents/data"
EIA_ABOUT_URL = "https://www.eia.gov/electricity/930-content/about.html"
ERCOT_URL = "https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json"
UTC = timezone.utc
EIA_WINDOW_DAYS = 7
EIA_MINIMUM_OBSERVED_AUTHORITIES = 50
EGRID_ARCGIS_ROOT = "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
GEOMETRY_TOLERANCE_DEGREES = 0.001
EGRID_CODES = frozenset("AKGD AKMS AZNM CAMX ERCT FRCC HIMS HIOA MROE MROW NEWE NWPP NYCW NYLI NYUP PRMS RFCE RFCM RFCW RMPA SPNO SPSO SRMV SRMW SRSO SRTV SRVC".split())
GWP_BASES = {"IPCC AR5": {"co2": 1, "ch4": 28, "n2o": 265},
             "IPCC AR6": {"co2": 1, "ch4": 29.8, "n2o": 273},
             "IPCC AR6 non-fossil methane": {"co2": 1, "ch4": 27, "n2o": 273}}
ACCOUNTING_BASIS = "annual generation-output average"
FACTOR_LIMITATIONS = ["Not a current or hourly observation", "Not a marginal or avoided-emissions factor",
                      "Does not track imports/exports or procurement", "Excludes transmission/distribution losses and upstream lifecycle emissions",
                      "A facility estimate requires actual electricity consumption and an appropriate regional assignment"]


class GridSourceError(ValueError):
    """A response cannot safely become a published grid dataset."""


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise GridSourceError("now must be timezone-aware")
    return now.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: object, *, field: str) -> float | None:
    if value is None or (isinstance(value, str) and value.strip() in ("", "NA", "N/A", "--", ".")):
        return None
    if isinstance(value, bool):
        raise GridSourceError(f"boolean in numeric field {field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GridSourceError(f"invalid number in {field}: {value!r}") from exc
    if not math.isfinite(number):
        raise GridSourceError(f"nonfinite number in {field}")
    return number


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)

    def handle_data(self, data):
        self.text_parts.append(data)


def egrid_release_date(page: bytes, year: int, revision: int) -> str | None:
    parser = _Links()
    parser.feed(page.decode("utf-8-sig"))
    text = " ".join(" ".join(parser.text_parts).split())
    # Bind the date to the selected edition's section, not an unrelated page date.
    section = re.search(rf"eGRID with {year} Data(.*?)(?:eGRID Historical Data|$)", text, re.I)
    if not section:
        return None
    label = rf"Revision\s*{revision}\s+Released" if revision else r"(?<!\d )Released"
    match = re.search(label + r"\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", section[1], re.I)
    if not match:
        return None
    return datetime.strptime(match[1], "%m/%d/%Y").date().isoformat()


def annual_grid_components(raw: bytes, year: int, *, metric: bool) -> dict[str, dict]:
    """Read the complete annual table, validating the workbook's stated units."""
    import openpyxl
    workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    required = {"YEAR", "SUBRGN", "SRNAME", "SRNGENAN", "SRCO2AN", "SRCH4AN", "SRN2OAN", "SRCO2EQA",
                "SRCO2RTA", "SRCH4RTA", "SRN2ORTA"}
    units = {"SRNGENAN": "MWh", "SRCO2AN": "metric tons" if metric else "tons",
             "SRCO2EQA": "metric tons" if metric else "tons",
             "SRCH4AN": "kg" if metric else "lbs", "SRN2OAN": "kg" if metric else "lbs",
             **{key: "kg/MWh" if metric else "lb/MWh" for key in ("SRCO2RTA", "SRCH4RTA", "SRN2ORTA")}}
    try:
        sheet = workbook[f"SRL{year % 100:02d}"]
        # The published Cornerstone workbook advertises A1 although the full
        # table is present. Streaming with reset dimensions reads the actual XML.
        sheet.reset_dimensions()
        rows = sheet.iter_rows(values_only=True)
        previous, columns = (), None
        for index, row in enumerate(rows):
            if required <= set(row):
                columns = {key: row.index(key) for key in required}
                for key, unit in units.items():
                    if columns[key] >= len(previous) or not str(previous[columns[key]]).endswith(f"({unit})"):
                        raise GridSourceError(f"Annual grid field {key} does not declare expected units {unit}")
                break
            if index >= 14:
                raise GridSourceError("Annual grid workbook has no recognizable component header")
            previous = row
        if columns is None:
            raise GridSourceError("Annual grid workbook has no component rows")
        out = {}
        for values in rows:
            if len(values) <= columns["SUBRGN"] or not values[columns["SUBRGN"]]:
                continue
            code = str(values[columns["SUBRGN"]]).strip()
            if code not in EGRID_CODES or code in out:
                raise GridSourceError(f"Unexpected or duplicate annual grid code {code}")
            row = {key: values[index] if index < len(values) else None for key, index in columns.items()}
            if row["YEAR"] != year or not isinstance(row["SRNAME"], str) or not row["SRNAME"].strip():
                raise GridSourceError("Annual grid row has mismatched year or missing name")
            for key in required - {"YEAR", "SUBRGN", "SRNAME"}:
                row[key] = _number(row[key], field=key)
                if row[key] is None or row[key] < 0 or (key == "SRNGENAN" and row[key] == 0):
                    raise GridSourceError(f"Invalid annual grid quantity {code} {key}")
            out[code] = row
        if out.keys() != EGRID_CODES:
            raise GridSourceError("Annual grid component table must contain exactly the 27 supported regions")
        return out
    finally:
        workbook.close()


def identify_annual_gwp(rows: dict[str, dict], *, metric: bool) -> dict:
    divisor = 1000 if metric else 2000
    matches = []
    for label, factors in GWP_BASES.items():
        deltas = []
        for row in rows.values():
            expected = row["SRCO2AN"] + (row["SRCH4AN"] * factors["ch4"] + row["SRN2OAN"] * factors["n2o"]) / divisor
            delta = abs(expected - row["SRCO2EQA"])
            if delta > max(.02, row["SRCO2EQA"] * 1e-8):
                break
            deltas.append(delta)
        if len(deltas) == len(rows):
            matches.append({"basis": label, "horizon_years": 100, "factors": factors,
                            "max_mass_difference": max(deltas), "mass_unit": "metric tonnes" if metric else "short tons",
                            "validation": "reconciled against all regional annual gas masses"})
    if len(matches) != 1:
        raise GridSourceError("Annual CO2e masses do not uniquely reconcile to a supported GWP basis; method review required")
    return matches[0]


def discover_egrid_workbook(page: bytes) -> tuple[str, int, int]:
    """Only EPA-hosted linked workbook editions can be selected."""
    parser = _Links()
    parser.feed(page.decode("utf-8-sig"))
    choices = []
    for href in parser.links:
        url = urljoin(EGRID_DISCOVERY_URL, href)
        parsed = urlparse(url)
        match = re.fullmatch(r"egrid(20\d\d)_data(?:_rev(\d+))?\.xlsx", parsed.path.rsplit("/", 1)[-1], re.I)
        if match and parsed.scheme == "https" and parsed.hostname in ("epa.gov", "www.epa.gov"):
            choices.append((int(match[1]), int(match[2] or 0), url))
    if not choices:
        raise GridSourceError("EPA release page contains no supported official eGRID workbook link")
    year, revision, url = max(choices)
    return url, year, revision


def collect_egrid(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    now = _utc(now)
    page = fetcher.get_bytes(EGRID_DISCOVERY_URL)
    url, year, revision = discover_egrid_workbook(page)
    if year >= now.year:
        raise GridSourceError("eGRID annual edition is not a completed historical data year")
    raw = fetcher.get_bytes(url)
    if not raw.startswith(b"PK"):
        raise GridSourceError("eGRID response is not an XLSX workbook")
    rates = parse_subregion_rates(raw, sheet=f"SRL{year % 100:02d}")
    components = annual_grid_components(raw, year, metric=False)
    gwp = identify_annual_gwp(components, metric=False)
    published_at = egrid_release_date(page, year, revision)
    # Any change in the 27-region geography needs an explicit schema review.
    if len(rates) != 27:
        raise GridSourceError(f"eGRID expected 27 subregions; found {len(rates)}")
    digest = hashlib.sha256(raw).hexdigest()
    edition = f"eGRID {year}" + (f" rev{revision}" if revision else "")
    for code, row in rates.items():
        if row["data_year"] != year:
            raise GridSourceError(f"eGRID link year {year} disagrees with workbook row {code}")
        row.update(source_id="egrid", source_url=url, workbook_sha256=digest,
                   publisher="US EPA", license="Public domain", source_role="official historical baseline",
                   unit="lb CO2e/MWh", co2e_rate_kg_per_mwh=row["co2e_rate_lb_per_mwh"] * .45359237,
                   accounting_basis=ACCOUNTING_BASIS, gwp_basis=gwp["basis"], gwp_factors=gwp["factors"],
                   gwp_horizon_years=100,
                   observation_start=f"{year}-01-01T00:00:00Z", observation_end=f"{year+1}-01-01T00:00:00Z",
                   published_at=published_at, limitations=FACTOR_LIMITATIONS,
                   units_note=f"eGRID SRL{year % 100:02d}: SRCO2RTA-style rate columns are lb CO2/MWh; "
                              "SRCO2AN/SRCO2EQA are SHORT TONS per year")
    return SourceResult(
        source_id="egrid", label="EPA eGRID annual emissions rates", url=url,
        data={"rates": rates, "source": {"edition": edition, "data_year": year,
              "url": url, "workbook_sha256": digest, "checked_at": _stamp(now),
              "discovery_url": EGRID_DISCOVERY_URL, "sheet": f"SRL{year % 100:02d}",
              "publisher": "US EPA", "license": "Public domain", "published_at": published_at,
              "accounting_basis": ACCOUNTING_BASIS, "gwp": gwp, "limitations": FACTOR_LIMITATIONS}},
        record_count=len(rates), observation_start=f"{year}-01-01T00:00:00Z",
        observation_end=f"{year + 1}-01-01T00:00:00Z", published_at=published_at, data_vintage=edition,
        coverage="27 US eGRID subregions, including Alaska and Hawaii",
        note="Annual location-based CO2e accounting rates; not hourly or marginal emissions. "
             "The edition check date is separate from the data year.",
    )


def geometry_urls(year: int, *, overlap: bool = False) -> dict[str, str]:
    layer = EGRID_ARCGIS_ROOT + f"eGRID{year}_" + ("Multiple_Subregions" if overlap else "Subregions") + "/FeatureServer/0"
    field = "MultipleSu" if overlap else "Subregion"
    return {
        "metadata": layer + "?f=json",
        "count": layer + "/query?" + urlencode({"where": "1=1", "returnCountOnly": "true", "f": "json"}),
        "geojson": layer + "/query?" + urlencode({
            "where": "1=1", "outFields": field, "returnGeometry": "true", "outSR": "4326",
            "f": "geojson", "maxAllowableOffset": str(GEOMETRY_TOLERANCE_DEGREES),
            "geometryPrecision": "6", "resultRecordCount": "2000",
        }),
    }


def _validate_geojson(payload: object, *, expected_count: int, overlap: bool) -> dict:
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise GridSourceError("EPA geometry response is not a GeoJSON FeatureCollection")
    if payload.get("exceededTransferLimit") or (payload.get("properties") or {}).get("exceededTransferLimit"):
        raise GridSourceError("EPA geometry response is truncated")
    features = payload.get("features")
    if not isinstance(features, list) or len(features) != expected_count:
        raise GridSourceError("EPA geometry feature count disagrees with the independent count query")
    seen = set()
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise GridSourceError("EPA geometry includes an invalid feature")
        properties = feature.get("properties") or {}
        if not overlap:
            code = properties.get("Subregion")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9]{2,5}", code) or code in seen:
                raise GridSourceError("EPA primary geometry has a missing or duplicate subregion code")
            seen.add(code)
            properties["subregion"] = code
        elif properties.get("MultipleSu") != "Yes":
            raise GridSourceError("EPA overlap geometry lacks its MultipleSu=Yes classification")
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        if kind not in ("Polygon", "MultiPolygon"):
            raise GridSourceError("EPA geometry must retain Polygon or MultiPolygon topology")
        polygons = [geometry.get("coordinates")] if kind == "Polygon" else geometry.get("coordinates")
        if not isinstance(polygons, list) or not polygons:
            raise GridSourceError("EPA polygon coordinates are empty")
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                raise GridSourceError("EPA polygon has no exterior ring")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise GridSourceError("EPA polygon contains an unclosed or incomplete ring")
                for point in ring:
                    if not isinstance(point, list) or len(point) != 2:
                        raise GridSourceError("EPA geometry coordinates are not two-dimensional lon/lat")
                    lon, lat = point
                    if (isinstance(lon, bool) or isinstance(lat, bool) or
                            not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)) or
                            not math.isfinite(lon) or not math.isfinite(lat) or
                            not -180 <= lon <= 180 or not -90 <= lat <= 90):
                        raise GridSourceError("EPA geometry coordinate is outside WGS84 lon/lat bounds")
    return payload


def _repair_polygon_parts(original):
    """Repair each part's linework, then union parts as GeoJSON displays them.

    make_valid on a whole overlapping MultiPolygon applies an even/odd rule
    that can remove its overlaps. Repairing individual polygons first and then
    taking their union preserves the union-of-parts coverage semantics.
    """
    import shapely
    from shapely import make_valid, union_all
    from shapely.validation import explain_validity

    originals = [original] if original.geom_type == "Polygon" else list(original.geoms)
    parts = []
    repaired_count = 0
    max_relative_change = 0.0
    reasons = []
    degenerate_parts = 0

    def polygon_parts(geometry):
        nonlocal degenerate_parts
        if geometry.geom_type == "Polygon":
            return [geometry]
        if geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
            return [part for child in geometry.geoms for part in polygon_parts(child)]
        degenerate_parts += 1
        return []

    for polygon in originals:
        fixed = polygon
        if not polygon.is_valid:
            repaired_count += 1
            reason = explain_validity(polygon)
            if len(reasons) < 5:
                reasons.append(reason)
            fixed = make_valid(polygon, method="linework")
            difference = abs(fixed.area - polygon.area)
            allowed = max(1e-12, abs(polygon.area) * 1e-8)
            if difference > allowed:
                raise GridSourceError(f"polygon linework repair changes area beyond tolerance: {difference:g} > {allowed:g}; {reason}")
            max_relative_change = max(max_relative_change, difference / max(abs(polygon.area), 1e-12))
        parts.extend(polygon_parts(fixed))
    if not parts:
        raise GridSourceError("polygon linework repair has no polygonal area")
    repaired_area = sum(part.area for part in parts)
    union = union_all(parts)
    if not union.is_valid or union.is_empty or union.geom_type not in ("Polygon", "MultiPolygon"):
        raise GridSourceError("union of repaired polygon parts is not valid polygonal coverage")
    if union.area > repaired_area + max(1e-12, repaired_area * 1e-8):
        raise GridSourceError("union of repaired polygon parts unexpectedly adds area")
    return union, {
        "repaired": not original.is_valid,
        "repair_method": "make_valid(method=linework) per Polygon, then union_all within the same subregion",
        "shapely_version": shapely.__version__, "geos_version": shapely.geos_version_string,
        "source_validity_reason": explain_validity(original), "part_validity_examples": reasons,
        "source_polygon_parts": len(originals), "repaired_polygon_parts": repaired_count,
        "degenerate_linework_parts": degenerate_parts,
        "source_additive_area_degrees2": original.area, "repaired_additive_area_degrees2": repaired_area,
        "union_area_degrees2": union.area, "duplicate_overlap_area_degrees2": max(0, repaired_area - union.area),
        "maximum_part_relative_area_change": max_relative_change,
        "part_relative_area_gate": 1e-8, "part_absolute_area_gate_degrees2": 1e-12,
    }


def simplify_geojson(payload: dict) -> dict:
    """Repair with area gates, then simplify without losing holes or islands."""
    from shapely import simplify
    from shapely.geometry import mapping, shape
    from shapely.validation import explain_validity

    features = []
    for feature in payload["features"]:
        original, repair = _repair_polygon_parts(shape(feature["geometry"]))
        original_polygons = [original] if original.geom_type == "Polygon" else list(original.geoms)
        original_holes = sum(len(poly.interiors) for poly in original_polygons)
        # GEOS can produce nested shells even with preserve_topology=True.
        # Try a smaller tolerance, then keep the valid unsimplified geometry.
        rejected = []
        for tolerance in (GEOMETRY_TOLERANCE_DEGREES, 0.0001, 0.00001, 0):
            simplified = simplify(original, tolerance, preserve_topology=True) if tolerance else original
            if simplified.is_empty or simplified.geom_type not in ("Polygon", "MultiPolygon"):
                reason = "polygonal coverage lost"
            elif not simplified.is_valid:
                reason = explain_validity(simplified)
            else:
                polygons = [simplified] if simplified.geom_type == "Polygon" else list(simplified.geoms)
                reason = ("island or hole counts changed" if len(polygons) != len(original_polygons) or
                          sum(len(poly.interiors) for poly in polygons) != original_holes else None)
            if reason is None:
                break
            rejected.append({"tolerance_degrees": tolerance, "reason": reason})
        properties = dict(feature["properties"])
        properties["geometry_valid"] = simplified.is_valid
        properties["geometry_transform"] = repair
        properties["geometry_transform"]["simplified_area_degrees2"] = simplified.area
        properties["geometry_transform"]["local_simplification_tolerance_degrees"] = tolerance
        properties["geometry_transform"]["rejected_simplification_attempts"] = rejected
        features.append({"type": "Feature", "properties": properties,
                         "geometry": mapping(simplified)})
    return {"type": "FeatureCollection", "features": features}


def collect_egrid_geometry(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    """Retain server-produced hole semantics plus the utility-ambiguity layer.

    GEOS simplifies with topology preservation. No ring flattening or additive
    interpretation of holes occurs. Both layers must succeed together.
    """
    now = _utc(now)
    _, year, _ = discover_egrid_workbook(fetcher.get_bytes(EGRID_DISCOVERY_URL))
    collections, hashes, urls, edits = [], [], [], []
    for overlap in (False, True):
        paths = geometry_urls(year, overlap=overlap)
        metadata = fetcher.get_json(paths["metadata"])
        count = fetcher.get_json(paths["count"])
        if not isinstance(metadata, dict) or metadata.get("error") or "fields" not in metadata:
            raise GridSourceError("EPA geometry layer metadata is unavailable")
        expected = count.get("count") if isinstance(count, dict) else None
        if not isinstance(expected, int) or isinstance(expected, bool) or not 0 < expected <= 2000:
            raise GridSourceError("EPA geometry count is invalid or requires unsupported pagination")
        if not overlap and expected != 27:
            raise GridSourceError(f"EPA geometry expected 27 primary regions, received {expected}")
        raw = fetcher.get_bytes(paths["geojson"])
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise GridSourceError("EPA geometry is not valid complete JSON") from exc
        hashes.append(hashlib.sha256(raw).hexdigest())
        collections.append(simplify_geojson(_validate_geojson(payload, expected_count=expected, overlap=overlap)))
        urls.append(paths["geojson"])
        edited = (metadata.get("editingInfo") or {}).get("dataLastEditDate")
        if isinstance(edited, (int, float)) and math.isfinite(edited):
            edits.append(datetime.fromtimestamp(edited / 1000, UTC))
    normalized = [json.dumps(item, separators=(",", ":"), sort_keys=True).encode() for item in collections]
    return SourceResult(
        source_id="egrid_geometry", label="EPA eGRID primary and overlapping grid regions", url=urls[0],
        data={"geojson": collections[0], "overlap_geojson": collections[1], "data_year": year,
              "edition": f"eGRID {year}", "tolerance_degrees": GEOMETRY_TOLERANCE_DEGREES,
              "server_tolerance_degrees": GEOMETRY_TOLERANCE_DEGREES,
              "boundary_buffer_degrees": 0.0021,
              "coordinate_precision_degrees": 0.000001,
              "sha256": hashes[0], "overlap_sha256": hashes[1], "source_urls": urls,
              "normalized_sha256": hashlib.sha256(normalized[0]).hexdigest(),
              "normalized_overlap_sha256": hashlib.sha256(normalized[1]).hexdigest(),
              "normalized_bytes": sum(len(raw) for raw in normalized),
              "simplification_method": "Area-gated per-Polygon linework repair; exact union within each region; simplify(preserve_topology=True)",
              "repaired": any(f["properties"]["geometry_transform"]["repaired"] for collection in collections for f in collection["features"]),
              "checked_at": _stamp(now), "binding_method": "GeoJSON polygon interior excluding holes; overlaps withheld",
              "boundary_policy": "Withhold ambiguous points near polygon boundaries and within the overlap layer"},
        record_count=len(collections[0]["features"]), data_vintage=f"eGRID {year} mapping files",
        published_at=_stamp(max(edits)) if edits else None,
        coverage=f"27 primary subregions and {len(collections[1]['features'])} multiple-subregion feature(s)",
        note="EPA polygon parts are repaired with strict area gates, then unioned only within each named region. "
             "Exterior rings, holes and MultiPolygons are retained. Server and local simplification each use at most "
             "0.001 degree; their errors can compound, requiring a 0.0021-degree boundary guard. "
             "Per-feature repair receipts and rejected simplification attempts are retained. "
             "This supports geographic context, not proof of the serving utility. Utility-overlap areas and "
             "near-boundary points must be withheld until utility evidence resolves the ambiguity.",
    )


def _csv_reader(raw: bytes, required: set[str], label: str) -> csv.DictReader:
    text = raw.decode("utf-8-sig")
    if text.lstrip().startswith("<"):
        raise GridSourceError(f"{label} returned HTML instead of CSV")
    reader = csv.DictReader(io.StringIO(text))
    missing = required.difference(reader.fieldnames or [])
    if missing:
        raise GridSourceError(f"{label} missing columns: {sorted(missing)}")
    return reader


def discover_eia_balance_files(raw: bytes, *, now: datetime) -> list[dict]:
    """Read the publisher's manifest, including both halves at a boundary."""
    now = _utc(now)
    lower = now - timedelta(days=EIA_WINDOW_DAYS)
    reader = _csv_reader(raw, {"FILENAME", "YEAR", "DESCRIPTION"}, "EIA manifest")
    selected = {}
    for row in reader:
        filename = row["FILENAME"]
        match = re.fullmatch(r"EIA930_BALANCE_(20\d\d)_(Jan_Jun|Jul_Dec)\.csv", filename)
        if not match:
            continue
        year, month = int(match[1]), 1 if match[2] == "Jan_Jun" else 7
        start = datetime(year, month, 1, tzinfo=UTC)
        end = datetime(year + (month == 7), 7 if month == 1 else 1, 1, tzinfo=UTC)
        if start > now:
            continue
        item = {"url": EIA_BASE_URL + filename, "filename": filename,
                "publisher_update_label": row.get("LAST_UPDATED"), "start": start, "end": end}
        if end > lower:
            if filename in selected and selected[filename] != item:
                raise GridSourceError(f"conflicting EIA manifest entries for {filename}")
            selected[filename] = item
    if not selected:
        raise GridSourceError("EIA manifest has no balance file covering the latest seven days")
    return sorted(selected.values(), key=lambda row: row["start"])


_EIA_REQUIRED = {"Balancing Authority", "UTC Time at End of Hour", "Demand (MW)",
                 "Net Generation (MW)", "Total Interchange (MW)", "Region"}
_FUEL_NAMES = {
    "Coal": "coal", "Natural Gas": "natural_gas", "Nuclear": "nuclear",
    "All Petroleum Products": "petroleum", "Hydropower Excluding Pumped Storage": "hydro",
    "Pumped Storage": "pumped_storage", "Solar without Integrated Battery Storage": "solar",
    "Solar with Integrated Battery Storage": "solar_with_storage",
    "Wind without Integrated Battery Storage": "wind", "Wind with Integrated Battery Storage": "wind_with_storage",
    "Battery Storage": "battery_storage", "Other Energy Storage": "other_storage",
    "Unknown Energy Storage": "unknown_storage", "Geothermal": "geothermal",
    "Other Fuel Sources": "other", "Unknown Fuel Sources": "unknown",
}


def parse_eia_balance(raw: bytes, *, now: datetime) -> tuple[dict[str, dict], dict[str, str]]:
    """Original reported stream only; forecasts never determine observation time."""
    now = _utc(now)
    window_end = now.replace(minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(days=EIA_WINDOW_DAYS)
    reader = _csv_reader(raw, _EIA_REQUIRED, "EIA balance")
    fuel_columns = {}
    for column in reader.fieldnames or []:
        prefix = "Net Generation (MW) from "
        if column.startswith(prefix) and not column.endswith(("(Imputed)", "(Adjusted)")):
            name = column[len(prefix):]
            fuel_columns[column] = _FUEL_NAMES.get(name, re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"))
    if not fuel_columns:
        raise GridSourceError("EIA balance contains no original fuel-generation columns")
    names = {key: column[len("Net Generation (MW) from "):] for column, key in fuel_columns.items()}
    regions: dict[str, dict] = {}
    for row in reader:
        if None in row:
            raise GridSourceError("EIA balance row has extra CSV fields")
        if any(value is None for value in row.values()):
            raise GridSourceError("EIA balance row is truncated")
        code = row["Balancing Authority"].strip()
        if not re.fullmatch(r"[A-Z0-9]{2,8}", code):
            raise GridSourceError(f"invalid EIA balancing authority code {code!r}")
        try:
            end = datetime.strptime(row["UTC Time at End of Hour"], "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=UTC)
        except ValueError as exc:
            raise GridSourceError(f"invalid EIA UTC hour-ending timestamp {row['UTC Time at End of Hour']!r}") from exc
        if end.minute or end.second:
            raise GridSourceError("EIA hour-ending timestamp is not aligned to an hour")
        start = end - timedelta(hours=1)
        if start < window_start or end > window_end:
            continue
        fuel = {key: _number(row[column], field=column) for column, key in fuel_columns.items()}
        demand = _number(row["Demand (MW)"], field="Demand (MW)")
        generation = _number(row["Net Generation (MW)"], field="Net Generation (MW)")
        interchange = _number(row["Total Interchange (MW)"], field="Total Interchange (MW)")
        has_observed = any(value is not None for value in (demand, generation, interchange, *fuel.values()))
        reading = {
            "interval_start_utc": _stamp(start), "interval_end_utc": _stamp(end), "interval_minutes": 60,
            "demand_mw": demand, "generation_mw": generation, "total_interchange_mw": interchange,
            "forecast_demand_mw": _number(row.get("Demand Forecast (MW)"), field="Demand Forecast (MW)"),
            "fuel_mix": {key: value for key, value in fuel.items() if value is not None},
            "missing_fuels": [key for key, value in fuel.items() if value is None],
            "quality": "reported" if has_observed else "missing",
            "source_timestamp": row["UTC Time at End of Hour"], "hour_convention": "END",
        }
        region = regions.setdefault(code, {"code": code, "parent_region": row["Region"], "by_time": {}})
        if region["parent_region"] != row["Region"]:
            raise GridSourceError(f"EIA authority {code} has conflicting parent regions")
        existing = region["by_time"].get(reading["interval_start_utc"])
        if existing is not None and existing != reading:
            raise GridSourceError(f"conflicting duplicate EIA hour: {code} {reading['interval_start_utc']}")
        region["by_time"][reading["interval_start_utc"]] = reading
    return regions, names


def _respondent_names(payload: object) -> dict[str, str]:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise GridSourceError("EIA respondent registry has an unsupported shape")
    rows = payload[0].get("data")
    if not isinstance(rows, list) or not rows:
        raise GridSourceError("EIA respondent registry is empty")
    return {row["id"]: row["name"] for row in rows if isinstance(row, dict) and row.get("id") and row.get("name")}


def collect_eia930(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    now = _utc(now)
    manifests = discover_eia_balance_files(fetcher.get_bytes(EIA_MANIFEST_URL), now=now)
    names = _respondent_names(fetcher.get_json(EIA_RESPONDENTS_URL))
    combined: dict[str, dict] = {}
    fuel_names: dict[str, str] = {}
    for manifest in manifests:
        regions, fuels = parse_eia_balance(fetcher.get_bytes(manifest["url"]), now=now)
        fuel_names.update(fuels)
        for code, region in regions.items():
            if code not in combined:
                combined[code] = region
                continue
            for stamp, reading in region["by_time"].items():
                existing = combined[code]["by_time"].get(stamp)
                if existing is not None and existing != reading:
                    raise GridSourceError(f"conflicting EIA records across files: {code} {stamp}")
                combined[code]["by_time"][stamp] = reading
    if len(combined) < EIA_MINIMUM_OBSERVED_AUTHORITIES:
        raise GridSourceError(f"EIA national coverage unexpectedly small: {len(combined)} authorities")
    result = []
    observed_count = 0
    observation_starts, observation_ends = [], []
    window_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(days=EIA_WINDOW_DAYS)
    for code, region in sorted(combined.items()):
        by_time = region.pop("by_time")
        for hour in range(EIA_WINDOW_DAYS * 24):
            start = window_start + timedelta(hours=hour)
            by_time.setdefault(_stamp(start), {
                "interval_start_utc": _stamp(start), "interval_end_utc": _stamp(start + timedelta(hours=1)),
                "interval_minutes": 60, "demand_mw": None, "generation_mw": None,
                "total_interchange_mw": None, "forecast_demand_mw": None, "fuel_mix": {},
                "missing_fuels": list(fuel_names), "quality": "missing", "hour_convention": "END",
            })
        readings = [row for _, row in sorted(by_time.items())]
        observed = [row for row in readings if row["quality"] == "reported"]
        observed_count += len(observed)
        start = observed[0]["interval_start_utc"] if observed else None
        end = observed[-1]["interval_end_utc"] if observed else None
        if start:
            observation_starts.append(start)
            observation_ends.append(end)
        demand = [row for row in readings if row["demand_mw"] is not None]
        region.update(name=names.get(code, code), source_id="eia930", unit="MW", readings=readings,
                      observation_start=start, observation_end=end, retrieved_at=_stamp(now),
                      latest_utc=end, readings_count=len(readings), reported_readings_count=len(observed),
                      latest_demand_mw=demand[-1]["demand_mw"] if demand else None,
                      latest_demand_utc=demand[-1]["interval_end_utc"] if demand else None,
                      stream="reported", hour_convention="END", fuel_names=fuel_names,
                      missing_readings_count=EIA_WINDOW_DAYS * 24 - len(observed),
                      expected_readings_count=EIA_WINDOW_DAYS * 24,
                      intensity_available=False)
        result.append(region)
    if not observed_count:
        raise GridSourceError("EIA returned no observed measurements in the latest seven days")
    observed_authorities = sum(region["reported_readings_count"] > 0 for region in result)
    if observed_authorities < EIA_MINIMUM_OBSERVED_AUTHORITIES:
        raise GridSourceError(f"EIA observed national coverage unexpectedly small: {observed_authorities} authorities; "
                              f"at least {EIA_MINIMUM_OBSERVED_AUTHORITIES} required")
    public_manifests = [{key: row[key] for key in ("url", "filename", "publisher_update_label")} for row in manifests]
    return SourceResult(
        source_id="eia930", label="EIA-930 national hourly electricity", url=EIA_MANIFEST_URL,
        data={"regions": result, "stream": "reported", "retained_days": EIA_WINDOW_DAYS,
              "files": public_manifests, "method_url": EIA_ABOUT_URL, "intensity_available": False,
              "authorities_with_observations": observed_authorities,
              "minimum_observed_authorities": EIA_MINIMUM_OBSERVED_AUTHORITIES},
        record_count=observed_count, observation_start=min(observation_starts), observation_end=max(observation_ends),
        data_vintage="Rolling seven days of reported hourly data",
        coverage=f"{len(result)} Lower-48 balancing authorities; generation-only authorities may have no demand",
        note="Original reported MW values; imputed and adjusted streams are retained only in raw source files. "
             "UTC hour-ending labels are shifted back one hour for interval starts. Forecasts do not establish "
             "freshness. Missing data remain null. Hourly carbon is withheld because current EIA carbon series "
             "are not sufficiently populated; no fuel-factor estimate is substituted.",
    )


_ERCOT_GENERATING = {"Coal and Lignite": "coal", "Natural Gas": "natural_gas", "Nuclear": "nuclear",
                     "Wind": "wind", "Solar": "solar", "Hydro": "hydro", "Other": "other"}
_ERCOT_STORAGE = {"Power Storage", "Power Storage Charging", "Power Storage Discharging"}


def parse_ercot(payload: object, *, now: datetime) -> dict:
    now = _utc(now)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict) or not payload["data"]:
        raise GridSourceError("ERCOT response has no populated data object")
    try:
        published_at = parse_offset_iso(payload["lastUpdated"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GridSourceError("ERCOT lastUpdated lacks a valid explicit UTC offset") from exc
    if published_at > now + timedelta(minutes=10):
        raise GridSourceError("ERCOT publisher update timestamp is in the future")
    by_time = {}
    days = []
    local_zone = ZoneInfo("America/Chicago")
    publisher_day = published_at.astimezone(local_zone).date()
    allowed_days = {publisher_day.isoformat(), (publisher_day - timedelta(days=1)).isoformat()}
    if len(payload["data"]) > 2 or not set(payload["data"]) <= allowed_days:
        raise GridSourceError("ERCOT data exceed the current and previous operating-day window")
    for day, intervals in sorted(payload["data"].items()):
        if not isinstance(intervals, dict) or not intervals:
            raise GridSourceError(f"ERCOT day {day} has no intervals")
        try:
            midnight = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=local_zone)
        except ValueError as exc:
            raise GridSourceError(f"invalid ERCOT day {day}") from exc
        day_start = midnight.astimezone(UTC)
        next_day = (midnight + timedelta(days=1)).astimezone(UTC)
        day_capacity = int((next_day - day_start).total_seconds() / 300)
        if len(intervals) > day_capacity:
            raise GridSourceError(f"ERCOT day {day} exceeds its five-minute interval capacity")
        day_rows = {}
        for label, body in intervals.items():
            start = parse_offset_iso(label)
            local_start = start.astimezone(local_zone)
            supplied_offset = datetime.fromisoformat(label.strip()).utcoffset()
            if supplied_offset != local_start.utcoffset():
                raise GridSourceError(f"ERCOT interval uses an invalid local UTC offset: {label}")
            if local_start.date() != midnight.date() or start.second or start.microsecond or start.minute % 5:
                raise GridSourceError(f"ERCOT interval label does not belong to its day or five-minute grid: {label}")
            if start > published_at or start > now + timedelta(minutes=5):
                raise GridSourceError("ERCOT measurement is later than its publisher update")
            if not isinstance(body, dict):
                raise GridSourceError(f"ERCOT invalid fuel breakdown at {label}")
            unknown = set(body).difference(_ERCOT_GENERATING).difference(_ERCOT_STORAGE)
            if unknown:
                raise GridSourceError(f"ERCOT unrecognized fuel categories: {sorted(unknown)}")
            values = {}
            for name in set(_ERCOT_GENERATING) | _ERCOT_STORAGE:
                value = body.get(name)
                if value is not None and (not isinstance(value, dict) or "gen" not in value):
                    raise GridSourceError(f"ERCOT malformed {name} value at {label}")
                values[name] = _number(value.get("gen") if isinstance(value, dict) else None, field=name)
                if name in _ERCOT_GENERATING and values[name] is not None and values[name] < 0:
                    raise GridSourceError(f"ERCOT negative non-storage generation at {label}: {name}")
            fuels = {key: values[name] for name, key in _ERCOT_GENERATING.items() if values[name] is not None}
            missing = [key for name, key in _ERCOT_GENERATING.items() if values[name] is None]
            components = ("Power Storage Charging", "Power Storage Discharging")
            storage = values["Power Storage"]
            if all(values[key] is not None for key in components):
                storage = sum(values[key] for key in components)
            row = {"interval_start_utc": _stamp(start), "interval_minutes": 5,
                   "demand_mw": None, "generation_mw": sum(fuels.values()) if not missing else None,
                   "fuel_mix": fuels, "missing_fuels": missing, "storage_mw": storage,
                   "storage_charging_mw": values[components[0]], "storage_discharging_mw": values[components[1]],
                   "quality": "reported" if not missing else "partial", "hour_convention": "START",
                   "source_timestamp": label, "intensity_lb_co2e_per_mwh": None}
            key = row["interval_start_utc"]
            if key in by_time:
                raise GridSourceError(f"duplicate ERCOT timestamp {key}")
            day_rows[key] = row
            by_time[key] = row
        latest = max(parse_offset_iso(row["source_timestamp"]) for row in day_rows.values())
        # Completed local days include 276/288/300 intervals across DST. The
        # current day is expected only through its latest published interval.
        end = next_day if next_day <= published_at else latest + timedelta(minutes=5)
        expected = int((end - day_start).total_seconds() / 300)
        missing_stamps = []
        for i in range(expected):
            key = _stamp(day_start + timedelta(minutes=5 * i))
            if key not in day_rows:
                missing_stamps.append(key)
                by_time[key] = {"interval_start_utc": key, "interval_minutes": 5, "demand_mw": None,
                                "generation_mw": None, "fuel_mix": {}, "missing_fuels": list(_ERCOT_GENERATING.values()),
                                "storage_mw": None, "quality": "missing", "hour_convention": "START",
                                "intensity_lb_co2e_per_mwh": None}
        days.append({"day": day, "expected_intervals": expected, "reported_intervals": len(day_rows),
                     "day_capacity_intervals": day_capacity,
                     "complete_fuel_intervals": sum(not row["missing_fuels"] for row in day_rows.values()),
                     "missing_intervals_utc": missing_stamps, "day_complete": next_day <= published_at,
                     "data_complete": len(missing_stamps) == 0 and all(not row["missing_fuels"] for row in day_rows.values())})
    readings = [row for _, row in sorted(by_time.items())]
    observed = [row for row in readings if row["quality"] != "missing" and row["fuel_mix"]]
    if not observed:
        raise GridSourceError("ERCOT contains no observed generation values")
    return {"code": "ERCO", "name": "Electric Reliability Council of Texas", "source_id": "ercot",
            "unit": "MW", "interval_minutes": 5, "readings": readings,
            "observation_start": observed[0]["interval_start_utc"], "observation_end": observed[-1]["interval_start_utc"],
            "latest_utc": observed[-1]["interval_start_utc"], "retrieved_at": _stamp(now),
            "published_at": _stamp(published_at), "readings_count": len(readings),
            "reported_readings_count": len(observed), "missing_readings_count": sum(len(day["missing_intervals_utc"]) for day in days),
            "days": days, "intensity_available": False,
            "generation_basis": "Sum of generating fuels; storage excluded and reported separately",
            "note": "Five-minute MW telemetry, not MWh. Hourly carbon is not calculated from unsupported fuel factors."}


def _ercot_window_validation(region: dict, *, now: datetime) -> dict:
    """A rolling telemetry window may shrink without losing source coverage.

    This exemption is deliberately stricter than the missing-data parser: a
    sparse or old response remains inspectable, but cannot explain a count
    drop. Local-day capacities include both DST transitions.
    """
    local_zone = ZoneInfo("America/Chicago")
    now = _utc(now)
    published_at = parse_offset_iso(region["published_at"])
    latest = parse_offset_iso(region["observation_end"])
    publisher_day = published_at.astimezone(local_zone).date()
    expected_days = sorted([publisher_day.isoformat(), (publisher_day - timedelta(days=1)).isoformat()])
    operating_days = [day["day"] for day in region["days"]]
    coverage = [{"day": day["day"], "expected_intervals": day["expected_intervals"],
                 "reported_intervals": day["reported_intervals"],
                 "complete_fuel_intervals": day["complete_fuel_intervals"],
                 "complete_fraction": day["complete_fuel_intervals"] / day["expected_intervals"]}
                for day in region["days"]]
    observation_age = (now - latest).total_seconds()
    publisher_lag = (published_at - latest).total_seconds()
    checks = {
        "two_expected_operating_days": operating_days == expected_days,
        "per_day_coverage_at_least_95_percent": all(day["complete_fraction"] >= .95 for day in coverage),
        "latest_operating_date_is_current": latest.astimezone(local_zone).date() == publisher_day == now.astimezone(local_zone).date(),
        "latest_within_15_minutes_of_publisher": 0 <= publisher_lag <= 15 * 60,
        "observation_within_two_hours": -5 * 60 <= observation_age <= 2 * 3600,
    }
    return {"record_count_semantics": "rolling_operating_days",
            "rolling_window_count_change_safe": all(checks.values()),
            "checks": checks, "expected_operating_days": expected_days,
            "operating_days": operating_days, "daily_coverage": coverage,
            "observation_age_seconds": observation_age,
            "publisher_lag_seconds": publisher_lag,
            "unique_utc_intervals": True, "bounded_local_day_intervals": True}


def collect_ercot(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    region = parse_ercot(fetcher.get_json(ERCOT_URL), now=now)
    return SourceResult(
        source_id="ercot", label="ERCOT five-minute fuel mix", url=ERCOT_URL,
        data={"regions": [region], "intensity_available": False,
              "validation": _ercot_window_validation(region, now=now)}, record_count=region["reported_readings_count"],
        observation_start=region["observation_start"], observation_end=region["observation_end"],
        published_at=region["published_at"], data_vintage="Current and previous ERCOT operating days",
        coverage="Texas ERCOT grid; five-minute reported generation by fuel",
        note="Missing intervals are explicit gaps. Storage is separate from generating fuels. "
             "MW values are not mislabeled as MWh. No unsupported hourly carbon factor is applied.",
    )
