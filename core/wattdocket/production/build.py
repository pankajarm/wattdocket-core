"""Build one coherent public release from validated source results.

Facility observations, public grid context and hypothetical scenarios have
different scopes. None is allowed to silently substitute for another.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

from .identity import condition_metadata, match_virginia_permits, permit_metadata, review_facilities
from .quality import validate_record_evidence, validate_disclosure_publication
from .disclosures import build_disclosures
from .facility_power import build_facility_power
from .explain_registry import build_explain_registry
from .storage import digest, json_bytes

LB_PER_TONNE = 2204.622621848776
SCENARIO_MW = 10.0
UTILIZATIONS = (0.45, 0.65, 0.80)
RANGE_KEYS = ("low", "base", "high")
SCHEMA_VERSION = "2.2"
BUILD_CODE_SHA256 = digest(Path(__file__).read_bytes())


def scenario_inputs(year):
    return {"load_mw": SCENARIO_MW, "capacity_basis": "GRID_MW",
            "utilization": dict(zip(RANGE_KEYS, UTILIZATIONS)),
            "hours_per_year": 8760, "factor_year": year,
            "input_source": "illustrative_preset", "facility_measurement": False}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def inside_ring(x, y, ring):
    inside = False
    for i, a in enumerate(ring):
        b = ring[i - 1]
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def distance_to_ring(x, y, ring):
    best = float("inf")
    for i, (ax, ay, *_) in enumerate(ring):
        bx, by = ring[i - 1][:2]
        dx, dy = bx - ax, by - ay
        u = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy))) if dx or dy else 0
        best = min(best, math.hypot(x - ax - u * dx, y - ay - u * dy))
    return best


class Geometry:
    def __init__(self, source: dict | None):
        self.tolerance = float((source or {}).get("boundary_buffer_degrees") or
                               float((source or {}).get("tolerance_degrees", 0.0001)) * 2)
        self.polygons = []
        self.overlaps = []
        for field, dest in (("geojson", self.polygons), ("overlap_geojson", self.overlaps)):
            for feature in (source or {}).get(field, {}).get("features", []):
                geo = feature.get("geometry") or {}
                prop = feature.get("properties") or {}
                if prop.get("geometry_valid") is False:
                    # Invalid overlap masks must block the whole binding lane.
                    if field == "overlap_geojson":
                        self.polygons = []
                        return
                    continue
                code = prop.get("subregion") or prop.get("Subregion")
                coordinates = geo.get("coordinates") or []
                polygons = [coordinates] if geo.get("type") == "Polygon" else coordinates if geo.get("type") == "MultiPolygon" else []
                for polygon in polygons:
                    if not polygon or len(polygon[0]) < 4:
                        continue
                    xs, ys = zip(*[p[:2] for p in polygon[0]])
                    dest.append((code, (min(xs), min(ys), max(xs), max(ys)), polygon))

    def _matches(self, polygons, lat, lon, uncertainty=0):
        matches, boundary = set(), False
        for code, (minx, miny, maxx, maxy), rings in polygons:
            # Source-coordinate uncertainty and generalized boundary error can
            # point in opposite directions; their bounds must be added.
            pad = self.tolerance + uncertainty
            if not (minx - pad <= lon <= maxx + pad and miny - pad <= lat <= maxy + pad):
                continue
            in_shape = inside_ring(lon, lat, rings[0]) and not any(inside_ring(lon, lat, hole) for hole in rings[1:])
            near = any(distance_to_ring(lon, lat, ring) <= pad for ring in rings)
            boundary = boundary or near
            if in_shape:
                matches.add(code or "overlap")
        return matches, boundary

    def resolve(self, lat, lon, accuracy_m=None):
        if (isinstance(lat, bool) or isinstance(lon, bool) or
                not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or
                not all(map(math.isfinite, (lat, lon))) or not -90 <= lat <= 90 or not -180 <= lon <= 180):
            return None, "missing_coordinates"
        if not self.polygons:
            return None, "geometry_unavailable"
        if accuracy_m is not None and (isinstance(accuracy_m, bool) or
                not isinstance(accuracy_m, (int, float)) or not math.isfinite(accuracy_m) or accuracy_m < 0):
            return None, "invalid_coordinate_accuracy"
        uncertainty = (accuracy_m / 111000 / max(abs(math.cos(math.radians(lat))), .01)
                       if isinstance(accuracy_m, (int, float)) and math.isfinite(accuracy_m) and accuracy_m > 0 else 0)
        overlap, near_overlap = self._matches(self.overlaps, lat, lon, uncertainty)
        if overlap or near_overlap:
            return None, "multiple_subregions_require_utility"
        found, boundary = self._matches(self.polygons, lat, lon, uncertainty)
        if boundary:
            return None, "near_boundary_requires_verification"
        if len(found) != 1:
            return None, "ambiguous" if found else "outside_coverage"
        return next(iter(found)), "point_in_polygon"


def _claim(cid, metric, label, unit, *, value="NOT_PUBLISHED", scope="facility",
           as_of=None, note="", citations=None, calc_id=None, bounds=None):
    numeric = isinstance(value, (int, float))
    if numeric and (not math.isfinite(value) or not (citations or calc_id)):
        raise ValueError("A numeric claim needs finite value and evidence or calculation")
    return {"id": cid, "metric": metric, "label": label, "value": value,
            "unit": unit, "scope": scope, "range_kind": "scenario" if scope == "scenario" else "reported",
            "badge": "MODELED" if scope == "scenario" else "REPORTED", "provisional": False,
            "as_of": as_of, "observation_end": as_of, "note": note, "unit_note": note,
            "range": dict(zip(RANGE_KEYS, bounds)) if bounds else None,
            "citations": citations or [], "calc_id": calc_id, "renderable": True,
            "render_block_reason": "", "drivers": [], "chain": [], "transforms": [], "sensitivity": []}


def _permit_matches(facility, permits):
    """Compatibility helper; names alone never establish regulatory identity."""
    return match_virginia_permits(facility, permits)["matches"]


def _permit_citation(c, permit, retrieved_at):
    sha = c.get("content_sha256")
    if not sha or len(sha) != 64 or not c.get("quote") or not c.get("page"):
        raise ValueError("Unverifiable permit evidence")
    return {"doc_sha256": sha, "page": c["page"], "line": None, "quote": c["quote"],
            "publisher": "Virginia DEQ", "dataset": "Air permit " + permit["registration"],
            "field": c.get("representation", "document") + "; " + c.get("page_derivation", ""),
            "representation": c.get("representation"), "verified": True,
            "verification_scope": "quoted_text_and_locator; legal_effectiveness_not_verified",
            "verifier_version": (permit.get("document") or {}).get("extraction_version", "va-deterministic-3"), "url": permit["permit_url"],
            "page_derivation": c.get("page_derivation"),
            **_document_freshness(permit.get("document") or {}),
            **{k: c[k] for k in ("representation", "native_page_text_sha256", "reviewed_page_text_sha256",
                                "transcription_review") if k in c},
            "retrieved_at": retrieved_at}


def _permit_review(permit):
    """Expose a reviewed original only for its exact registry document identity."""
    doc = permit.get("document") or {}
    review = doc.get("recovery_review")
    if not review or review.get("status") != "verified":
        return None
    if (review.get("review_version") != "va-reviewed-pdf-1"
            or review.get("registration") != permit.get("registration")
            or review.get("publisher_url") != permit.get("permit_url")
            or review.get("document_sha256") != doc.get("content_sha256")
            or doc.get("status") != "parsed"
            or doc.get("extraction_version") != "va-reviewed-pdf-1"
            or review.get("supersession_review", {}).get("current_legal_effectiveness") != "unknown"):
        raise ValueError("Recovered permit review does not identify the published document")
    claims = []
    for table in doc.get("equipment", []):
        cits = [_permit_citation(c, permit, doc.get("retrieved_at")) for c in table.get("citations", [])]
        if cits:
            claim = _claim("diesel-mw", "diesel_mw", "Generators in cited table", "MW nameplate",
                value=table["mw_total"], citations=cits, as_of=permit.get("issued_iso"),
                note="Only the cited equipment table; not a verified whole-site total or actual operating use.")
            claim.update(**_document_freshness(doc), evidence_scope="equipment_table_page", is_complete_fleet=False,
                         reviewed_table_scope=table.get("reviewed_table_scope"),
                         permit_metadata={**permit_metadata(permit), "recovery_review": review})
            claims.append(claim)
    if len(claims) > 1:
        raise ValueError("Separate reviewed equipment tables need distinct explanation identifiers")
    return {**permit_metadata(permit), "site": permit.get("site"), "url": permit["permit_url"],
            "content_sha256": doc["content_sha256"], **_document_freshness(doc),
            **{k: permit[k] for k in ("permit_history_review", "site_identity_review") if k in permit},
            "recovery_review": review, "conditions": [_public_permit_condition(permit, item) for item in doc.get("obligations", [])],
            "equipment_claims": claims}


def _public_permit_condition(permit, item):
    doc = permit.get("document") or {}
    citation = item.get("citation") or {}
    _permit_citation(citation, permit, doc.get("retrieved_at"))
    return {"metric": item["metric"], "value": item["value"], "unit": item["unit"],
        "registration": permit["registration"], "page": citation["page"], "quote": citation["quote"], "url": permit["permit_url"],
        "retrieved_via": doc.get("retrieved_via"), "content_sha256": doc["content_sha256"], "issued_at": permit.get("issued_iso"),
        **permit_metadata(permit), **condition_metadata(item), "page_derivation": citation.get("page_derivation"),
        **_document_freshness(doc),
        **({"permit_review_ref": {"registration": permit["registration"], "document_sha256": doc["content_sha256"]},
            "legal_effectiveness": permit["permit_history_review"]["legal_effectiveness"]} if permit.get("permit_history_review") else {}),
        **({"recovery_review": doc["recovery_review"]} if doc.get("recovery_review", {}).get("status") == "verified" else {}),
        **{k: item[k] for k in ("reviewed_condition_scope", "reviewed_operating_purpose", "reviewed_period") if k in item},
        **{k: citation[k] for k in ("representation", "native_page_text_sha256", "reviewed_page_text_sha256", "transcription_review") if k in citation}}


def _document_freshness(document):
    freshness = document.get("freshness")
    if not freshness:
        freshness = ("archived" if document.get("archive_source") == "retained_repository_reader_text" else
                     "cached" if document.get("cache_reused") or document.get("receipt", {}).get("cache_hit") else "current")
    return {"freshness": freshness, "retrieved_at": document.get("retrieved_at"),
            "archived_by_at": document.get("archived_by_at"),
            "latest_fetch_status": document.get("latest_fetch_status"),
            "latest_fetch_error": document.get("latest_fetch_error"),
            "latest_fetch_at": document.get("latest_fetch_attempt_at"),
            "representation": document.get("representation"),
            "extraction_version": document.get("extraction_version")}


def make_drawer(card, claim):
    citations = []
    for i, c in enumerate(claim["citations"]):
        citations.append({"id": claim["id"] + f"-cit-{i}", "claim_id": claim["id"],
                          "document_sha256": c["doc_sha256"], "publisher": c["publisher"],
                          "document_id": c["dataset"], "page": c.get("page"), "line": None,
                          "quote": c["quote"], "matched_text": c["quote"], "verified": c["verified"],
                          # Rebuilding a card cannot renew the underlying verification.
                          "verified_at_utc": c.get("verified_at_utc") or c.get("retrieved_at"),
                          "verification_scope": c.get("verification_scope", "source_row_integrity"),
                          "verifier_version": c["verifier_version"], "url": c.get("url"),
                          **{k: c.get(k) for k in ("freshness", "retrieved_at", "archived_by_at", "latest_fetch_status",
                                                  "latest_fetch_error", "latest_fetch_at", "representation", "page_derivation")}})
    numeric = isinstance(claim["value"], (int, float))
    bounds = claim["range"] or {}
    return {"claim_id": claim["id"], "metric": claim["metric"], "label": claim["label"],
            "headline": f'{claim["value"]:,.1f}' if numeric else "Not published",
            "unit": claim["unit"], "badge": claim["badge"], "provisional": False,
            "scope": claim["scope"], "range_kind": claim["range_kind"], "note": claim["note"],
            **{k: claim.get(k) for k in ("freshness", "retrieved_at", "archived_by_at", "latest_fetch_status",
                                        "latest_fetch_error", "latest_fetch_at", "representation", "evidence_scope", "is_complete_fleet", "permit_metadata")},
            **{k: bounds.get(k) for k in RANGE_KEYS}, "inputs": claim.get("inputs"),
            "as_of_utc": claim["as_of"], "receipt_count": len(citations),
            "citation": citations[0] if citations else None, "extra_citations": citations[1:],
            "chain": {"calc_id": claim["calc_id"] or "not_applicable", "name": "wattdocket.production",
                      "version": SCHEMA_VERSION, "code_sha256": BUILD_CODE_SHA256,
                      "code_scope": "production/build.py", "steps": [{"line": x, "kind": "compute"} for x in claim["chain"]]},
            "transforms": [{"label": "Method", "value": x} for x in claim["transforms"]],
            "sensitivity": [], "drivers": [claim["note"]] if claim["note"] else []}


def make_operator(card):
    facility = card["facility"]
    scenario = next((c for c in card["claims"] if c["id"] == "scenario-carbon"), None)
    value = scenario["value"] if scenario and isinstance(scenario["value"], (int, float)) else None
    sources = [{"supports": c["label"], "publisher": s["publisher"], "document": s["dataset"],
                "locator": str(s.get("page") or s.get("field") or ""), "quote": s["quote"],
                "doc_sha256": s["doc_sha256"], "url": s.get("url"), "verified": s["verified"],
                **{k: s.get(k) for k in ("verification_scope", "retrieved_at", "freshness", "archived_by_at",
                                         "latest_fetch_status", "representation")}}
               for c in card["claims"] for s in c["citations"]]
    missing = "No operating record for this facility was found in the collected sources."
    return {"release_id": card["release_id"], "data_status": card["data_status"],
            "disclosures": card.get("disclosures", []),
            "built_at": card["data_status"]["built_at"], "scope": "reference_scenario",
            "observation_start": card["grid"].get("observation_start"),
            "observation_end": card["grid"].get("observation_end"),
            "scenario_inputs": scenario.get("inputs") if scenario else None,
            "as_of": card["as_of"], "calc_version": "wattdocket.production@" + SCHEMA_VERSION,
            "facility_id": facility["id"], "facility_label": facility["name"], "synthetic_meter_data": False,
            "methods": [{"key": "location", "label": "10 MW reference scenario", "tonnes": value,
                         "scope": "scenario", "inputs": scenario.get("inputs") if scenario else None,
                         "observation_start": card["grid"].get("observation_start"),
                         "observation_end": card["grid"].get("observation_end"),
                         "note": "Hypothetical annual grid emissions; not measured facility emissions.", "tone": "teal"},
                        {"key": "market", "label": "Market-based", "tonnes": None,
                         "note": "Withheld: verified procurement records are unavailable.", "tone": "amber"},
                        {"key": "hourly", "label": "24/7 hourly matched", "tonnes": None,
                         "note": "Withheld: hourly facility load and procurement are unavailable.", "tone": "amber"}],
            "matching_note": "The reference scenario is separate from facility accounting. Operating measurements are unavailable.",
            "matched_pct": None, "compliance": [
                {"key": "generator_hours", "label": "Actual generator run hours", "value": "—", "unit": "hr/yr", "sub": missing, "pct": None, "mark_pct": None, "tone": "amber"},
                {"key": "water", "label": "Actual water use", "value": "—", "unit": "gal/day", "sub": missing, "pct": None, "mark_pct": None, "tone": "amber"}],
            "peak": {"key": "peak", "label": "Scenario load", "value": "10", "unit": "MW", "sub": "User-independent reference assumption, not a facility measurement", "pct": None, "mark_pct": None, "tone": "teal"},
            "levers": [], "heatmap": {"months": [], "hours": 0, "cells": [], "note": "Hourly matched-energy records are unavailable."},
            "permit_envelope": None, "permit_limits": facility.get("permit_limits", []),
            "method": {**card["grid"], "scope": "scenario", "inputs": scenario.get("inputs") if scenario else None,
                       "chain": scenario["chain"] if scenario else [], "calc_id": scenario["calc_id"] if scenario else None},
            "sources": sources, "export": [{"label": "Public evidence and source dates", "detail": "Reported facts and scenarios are labeled separately."}],
            "footer": "Public records only. Missing measurements are withheld; scenarios are illustrative."}


def build_release(states: dict, release_id: str, built_at: str):
    results = {key: value["result"] for key, value in states.items() if value.get("result")}
    if "echo" not in results or "egrid" not in results:
        raise ValueError("A release requires successful national facility and annual grid sources")
    sources = [{k: v for k, v in state.items() if k not in ("result", "receipts", "failed_attempt_receipts")}
               for _, state in sorted(states.items())]
    data_status = {"release_id": release_id, "built_at": built_at, "sources": sources}
    rates = results["egrid"]["data"]["rates"]
    geometry_data = results.get("egrid_geometry", {}).get("data")
    rate_years = {row.get("data_year") for row in rates.values()}
    if geometry_data and (geometry_data.get("data_year") is None or rate_years != {geometry_data["data_year"]}):
        # Annual factors and boundaries are separate sources/cadences. Do not
        # assign a new edition's rates using an older edition's footprint.
        geometry_data = None
    geometry = Geometry(geometry_data)
    permits = results.get("virginia_permits", {}).get("data", {}).get("permits", [])
    records = list(results["echo"]["data"]["facilities"])
    existing_ids = {r["id"] for r in records}
    records += [r for r in results.get("virginia_site_registry", {}).get("data", {}).get("targeted_epa_facilities", []) if r["id"] not in existing_ids]
    records += results.get("osm", {}).get("data", {}).get("facilities", [])
    from .permit_history import apply_permit_history
    permits = apply_permit_history(permits, records, states, built_at=built_at)
    disclosure_data, record_disclosures, disclosure_coverage, disclosure_sources = build_disclosures(
        states, records, release_id, built_at)
    # Capture metadata remains immutable in source state; this release's product
    # notes describe separately reviewed coverage links, never numeric allocation.
    for source in sources:
        if source['id'] == 'google_water2025':
            from .sources_operator_reports import METHOD_NOTE
            source['coverage'] = '40 named Google locations worldwide; aggregates retained separately; reviewed record links do not allocate totals'
            source['note'] = METHOD_NOTE + ' Named reporting locations may include multiple buildings. The source itself supplies no EPA identifier or meter-to-record match. Separately reviewed coverage links are shown with identity evidence; location totals remain unallocated. Excludes seawater and brackish water; no 2026 actual-use claim.'
    reviews = review_facilities(records)
    alternative_result = results.get("cornerstone_egrid2024", {})
    alternative_data = alternative_result.get("data", {})
    alternative_rates = alternative_data.get("rates", {})
    files, entries, cards, drawers, operators = {}, [], {}, {}, {}
    citations = {}
    base = f"/data/releases/{release_id}"
    for code, row in sorted(rates.items()):
        raw = json_bytes(row)
        path = f"evidence/egrid/{code}.json"
        files[path] = raw
        citations[code] = {"doc_sha256": digest(raw), "page": None, "line": None,
                           "quote": raw.decode().strip(), "publisher": "US EPA",
                           "dataset": f"eGRID {row['data_year']} normalized workbook row: {code}",
                           "field": "SRCO2EQA × 2000 / SRNGENAN; workbook " + row.get("workbook_sha256", ""),
                           "verified": True, "verifier_version": "workbook-row-2", "url": base + "/" + path,
                           "publisher_url": row.get("source_url"), "retrieved_at": states["egrid"].get("retrieved_at"),
                           "verification_scope": "published_workbook_row_and_unit_conversion"}
    for code, row in sorted(alternative_rates.items()):
        files[f"evidence/cornerstone-egrid2024/{code}.json"] = json_bytes(row)
    if alternative_data:
        files["grid/annual-comparison.json"] = json_bytes({
            "release_id": release_id, "source": alternative_data.get("source"),
            "validation": alternative_data.get("validation"),
            "scope": "regional_comparison_only", "geography_binding_verified": False,
            "note": "Independent 2024 reconstruction by named subregion. No 2024 facility-to-region assignment is established.",
            "regions": alternative_rates})
    for raw_facility in records:
        fid = raw_facility["id"]
        key = slug(fid)
        if key in cards:
            raise ValueError("Duplicate published facility identity: " + key)
        origin = "osm" if fid.startswith("osm:") else "echo"
        state = states[raw_facility.get("source_lane") or origin]
        source_url = raw_facility.get("url") or "https://echo.epa.gov/detailed-facility-report?fid=" + str(raw_facility.get("registry_id") or raw_facility.get("source_record_id") or fid.split(":", 1)[1])
        source_label = "OpenStreetMap" if origin == "osm" else "EPA ECHO"
        match_review = match_virginia_permits(raw_facility, permits)
        matches = match_review["matches"]
        review = reviews[fid]
        for link in match_review["identity_match"].get("reviewed_links", []):
            conflict = link.get("combined_address_conflict")
            if conflict:
                match_review["conflicts"].append({"reason_codes": ["regulatory_program_address_conflict"],
                    "note": "The consolidated FRS address conflicts with the reviewed DEQ/AIR physical-site address; point and grid assignment are withheld.",
                    "details": conflict, "record_id": fid})
                location = review["location_quality"]
                location.update(status="suspect", map_eligible=False)
                location["reasons"].append("The consolidated FRS address conflicts with the reviewed DEQ/AIR physical-site address.")
                location["reason_codes"].append("regulatory_program_address_conflict")
        facility = {**{k: raw_facility.get(k) for k in ("name", "operator", "city", "state", "zip", "lat", "lon", "naics", "status", "registry_id", "source_record_id")},
                    "id": fid, "site_id": None, "address": raw_facility.get("address_line") or "",
                    "source": origin, "source_lane": raw_facility.get("source_lane") or origin, "source_label": source_label, "source_url": source_url,
                    "source_license": raw_facility.get("license"), "source_attribution": raw_facility.get("attribution"),
                    "status_basis": raw_facility.get("status_basis") or ("EPA AIR program record status" if origin == "echo" else "Community-reported status"),
                    "is_operating_datacenter_verified": False,
                    "class_verified": bool(matches),
                    "class_label": "Virginia regulator lists this as a data center" if matches else "Community-mapped data center" if origin == "osm" else "EPA computing-industry listing; facility type unverified",
                    "class_evidence": "Virginia DEQ reviewed regulator identity match" if matches else "Source classification only",
                    "class_match_method": match_review["match_basis"], "identity_match": match_review["identity_match"],
                    "identity_conflicts": match_review["conflicts"], "identity_candidates": match_review["candidate_matches"],
                    **review,
                    "site_permit_count": len(raw_facility.get("air_records", [])), "sibling_ids": [],
                    "permit_limits": [], "permit_reviews": [], "record_summary": [
                        {"label": "Listing source", "value": source_label, "source_url": source_url},
                        {"label": "EPA AIR program status" if origin == "echo" else "Community-reported status", "value": raw_facility.get("status") or "unknown", "source_url": source_url}]}
        if origin == "echo":
            for program, ident in raw_facility.get("program_ids", {}).items():
                if ident:
                    facility["record_summary"].append({"label": program.upper() + " program ID", "value": ident, "source_url": source_url})
        else:
            facility["attribution"] = "© OpenStreetMap contributors"
            facility["license"] = "ODbL-1.0"
        from .texas_evidence import for_facility as texas_documents
        facility["documents"] = texas_documents(raw_facility, results.get("texas_permits", {}).get("data", {}))
        facility["coordinate_accuracy_m"] = raw_facility.get("coordinate_accuracy_m")
        sub, binding = (geometry.resolve(facility["lat"], facility["lon"], facility["coordinate_accuracy_m"])
                        if facility["location_quality"]["map_eligible"] else (None, "location_requires_review"))
        rate = rates.get(sub, {}).get("co2e_rate_lb_per_mwh")
        grid_row = rates.get(sub, {})
        grid = {"subregion": sub, "subregion_name": grid_row.get("name"), "utility": None,
                "balancing_authority": "ERCO" if sub == "ERCT" else None,
                "rate_lb_per_mwh": rate, "intensity_state": "SETTLED", "factor_set_id": f"epa-egrid-{grid_row.get('data_year', 'unresolved')}",
                "factor_set_sha256": grid_row.get("workbook_sha256", ""), "binding_method": binding,
                "data_vintage": str(grid_row.get("data_year") or ""), "source_label": "Latest official EPA annual baseline",
                "factor_publisher": "US EPA", "metric": "CO2e",
                "gwp_basis": grid_row.get("gwp_basis") or ("IPCC AR5" if grid_row.get("data_year") == 2023 else "not_documented"),
                "accounting_basis": "annual generation-output average; historical regional context",
                "source_url": grid_row.get("source_url") or results["egrid"].get("url"),
                "evidence_url": citations[sub]["url"] if sub in citations else None,
                "published_at": results["egrid"].get("published_at"),
                "observation_start": results["egrid"].get("observation_start"),
                "observation_end": results["egrid"].get("observation_end"), "retrieved_at": states["egrid"].get("retrieved_at")}
        if sub in alternative_rates:
            alt = alternative_rates[sub]
            grid["alternative"] = {**alt, "rate_lb_per_mwh": alt["co2e_rate_lb_per_mwh"],
                "source_label": "Cornerstone independent 2024 regional comparison",
                "evidence_url": base + f"/evidence/cornerstone-egrid2024/{sub}.json",
                "source_url": alternative_result.get("url"), "published_at": alternative_result.get("published_at"),
                "retrieved_at": states["cornerstone_egrid2024"].get("retrieved_at"),
                "scope": "regional_comparison_only", "geography_binding_verified": False,
                "validation": alternative_data.get("validation"),
                "note": "The same named region in an independent 2024 reconstruction; not a verified 2024 assignment for this record."}
        claims = [
            _claim("power-mw", "power_mw", "Actual electricity demand", "MW", note="Facility meter readings or a disclosed demand figure are not available in the collected sources."),
            _claim("facility-carbon", "carbon_t", "Reported facility emissions", "t CO2e/yr", note="No verified annual facility-emissions observation is included in this record."),
            _claim("water-gal", "water_gal", "Actual water use", "gal/day", note="No facility-level water-use measurement is available in the collected sources."),
            _claim("diesel-mw", "diesel_mw", "Backup generator capacity", "MW", note="No verified whole-facility generator capacity is available in this record.")]
        for permit in matches:
            doc = permit.get("document") or {}
            permit_info = permit_metadata(permit)
            reviewed = _permit_review(permit)
            if reviewed:
                facility["permit_reviews"].append(reviewed)
                permit_info["recovery_review"] = reviewed["recovery_review"]
            for item in doc.get("obligations", []):
                facility["permit_limits"].append(_public_permit_condition(permit, item))
        # Equipment totals are valid only for their cited table. Never sum
        # revisions/pages into an invented campus total or emissions envelope.
        latest = max(matches, key=lambda p: (p.get("issued_iso") or "", tuple(int(n) for n in p["registration"].split("-")))) if matches else None
        if latest and latest.get("document", {}).get("status") == "parsed":
            tables = latest["document"].get("equipment", [])
            if len(tables) == 1:
                table = tables[0]
                cits = [_permit_citation(c, latest, latest["document"].get("retrieved_at")) for c in table.get("citations", [])]
                if cits:
                    claims[3] = _claim("diesel-mw", "diesel_mw", "Generators in cited table", "MW nameplate",
                        value=table["mw_total"], citations=cits, as_of=latest.get("issued_iso"),
                        note="Only the cited equipment table; not a verified whole-site total or actual operating use.")
                    claims[3]["chain"] = ["Sum the quoted equipment row counts × their kW rating; divide by 1000 to obtain MW."]
                    claims[3].update(**_document_freshness(latest["document"]),
                                     evidence_scope="equipment_table_page", is_complete_fleet=False,
                                     permit_metadata={**permit_metadata(latest),
                                         **({"recovery_review": latest["document"]["recovery_review"]}
                                            if _permit_review(latest) else {})})
        if rate is not None:
            year = grid_row["data_year"]
            observed = f"{year}-12-31"
            values = [round(SCENARIO_MW * 8760 * u * rate / LB_PER_TONNE, 3) for u in UTILIZATIONS]
            power = _claim("scenario-power", "power_mw", "Illustrative calculator preset", "MW", value=SCENARIO_MW,
                           scope="scenario", calc_id="reference-grid-load-10mw-v3", note="An editable calculator preset, unrelated to this facility's measured demand.")
            carbon = _claim("scenario-carbon", "carbon_t", "Scenario annual emissions", "t CO2e/yr", value=values[1],
                            scope="scenario", as_of=observed, citations=[citations[sub]],
                            calc_id=f"reference-10mw-utilization-045-065-080-egrid-{year}-{sub}-v3", bounds=values,
                            note=f"10 MW grid-side reference, 45–80% utilization (65% central scenario), using {year} annual grid rates. Scenario bounds, not confidence intervals.")
            carbon["chain"] = ["10 MW chosen grid-side load × 8760 hours × 65% chosen utilization = 56,940 MWh/year",
                               f"56,940 MWh × {rate:.6f} lb CO2e/MWh ÷ {LB_PER_TONNE} lb/tonne = {values[1]} tonnes/year",
                               "Lower and upper scenarios use 45% and 80% utilization; no PUE is added to a grid-side load."]
            for claim in (power, carbon):
                claim["inputs"] = scenario_inputs(year)
                claim["observation_start"] = results["egrid"].get("observation_start")
                claim["observation_end"] = results["egrid"].get("observation_end")
                claim["observation_scope"] = "factor_period; scenario_is_not_an_observation"
            claims.extend((power, carbon))
        supporting_sources = {raw_facility.get("source_lane") or origin}
        if any(p.get("site_identity_review") for p in matches):
            supporting_sources.add("virginia_site_registry")
        disclosed = record_disclosures.get(fid, [])
        if disclosed:
            supporting_sources.update(group["location"]["source_id"] for group in disclosed)
            supporting_sources.update(group["link"].get("identity_source_id", "disclosure_identity") for group in disclosed)
        if matches or match_review["candidate_matches"]:
            supporting_sources.add("virginia_permits")
        if facility["documents"]:
            supporting_sources.add("texas_permits")
        if sub is not None:
            supporting_sources.update(("egrid", "egrid_geometry"))
            if sub in alternative_rates:
                supporting_sources.add("cornerstone_egrid2024")
        card_status = {"release_id": release_id, "built_at": built_at,
                       "sources": [s for s in sources if s["id"] in supporting_sources]}
        card = {"schema_version": SCHEMA_VERSION, "generated_by": "wattdocket.production@" + SCHEMA_VERSION, "release_id": release_id,
                "as_of": state.get("retrieved_at"), "facility": facility, "grid": grid, "claims": claims,
                "disclosures": disclosed,
                "data_status": card_status, "hourly": None, "hero": False,
                "verdict": {"sentence": "This record combines public facility listings and available permit evidence. Actual electricity demand and water use remain unavailable; any reference scenario below is hypothetical.",
                            "source": "template", "degraded": False}}
        if disclosed:
            card["verdict"]["sentence"] = "Reported annual disclosures are linked below with their reviewed building or campus scope. Annual electricity is not peak demand; related campus totals are not allocated to this record. The grid calculator remains hypothetical."
        entries.append({"id": fid, "site_id": None, "name": facility["name"], "address_line": facility["address"],
                        **{k: facility.get(k) for k in ("city", "state", "zip", "lat", "lon", "registry_id", "source", "source_lane", "source_label", "source_url", "class_verified", "class_label", "class_match_method", "identity_match", "location_quality", "duplicate_candidates", "site_identity", "status")},
                        "source_record_id": raw_facility.get("source_record_id"), "hero": False})
        cards[key] = card
        drawers[key] = {slug(c["id"]): make_drawer(card, c) for c in claims}
        operators[key] = make_operator(card)
    regions = results.get("eia930", {}).get("data", {}).get("regions", [])
    ercot = results.get("ercot", {}).get("data", {}).get("regions", [])
    grid_index = []
    for region in regions:
        code = region["code"]
        r = {**region, "release_id": release_id}
        if code == "ERCO" and ercot:
            r["five_minute"] = ercot[0]
        files[f"grid/{code.lower()}.json"] = json_bytes(r)
        readings = r["readings"]
        demands = [x for x in readings if x.get("demand_mw") is not None]
        grid_index.append({"code": code, "name": r["name"], "source_id": "eia930", "unit": "MW",
                           "latest_utc": r.get("observation_end"), "readings_count": len(readings),
                           "latest_demand_mw": demands[-1]["demand_mw"] if demands else None})
    if ercot:
        files["grid/ercot-five-minute.json"] = json_bytes(ercot[0])
    water_disclosures = results.get("google_water2025", {}).get("data")
    if water_disclosures:
        files["operator-water.json"] = json_bytes({
            **water_disclosures, "release_id": release_id, "built_at": built_at,
            "scope": "operator_reported_location", "facility_binding_verified": False,
            "retrieved_at": states["google_water2025"].get("retrieved_at")})
    if disclosure_data["locations"]:
        files["facility-observations.json"] = json_bytes(disclosure_data)
    power_files, power_coverage = build_facility_power(states, release_id, built_at)
    files.update(power_files)
    va_parsed = sum(p.get("document", {}).get("status") == "parsed" for p in permits)
    recovered = [row for permit in permits if (row := _permit_review(permit))]
    for row in recovered:
        row["inventory_record_ids"] = [c["facility"]["id"] for c in cards.values()
            if any(p["registration"] == row["registration"] and p["content_sha256"] == row["content_sha256"]
                   for p in c["facility"].get("permit_reviews", []))]
    data_status["permit_recovery"] = {"documents": recovered,
        "scope": "Reviewed original PDFs; exact regulator identity links only. Document recovery does not establish current legal effectiveness."}
    tx_documents = results.get("texas_permits", {}).get("data", {}).get("documents", [])
    tx_parsed = results.get("texas_permits", {}).get("data", {}).get("documents_ok", 0)
    candidate_groups = {g["group_id"] for r in reviews.values() for g in r["duplicate_candidates"]
                        if g["basis"] == "same_normalized_address_city_state"}
    coverage = {"facility_records": len(entries), "regulator_verified": sum(e["class_verified"] for e in entries),
                "unverified_records": sum(not e["class_verified"] for e in entries),
                "with_coordinates": sum(e["lat"] is not None and e["lon"] is not None for e in entries),
                "map_eligible_records": sum(e["location_quality"]["map_eligible"] for e in entries),
                "suspect_location_records": sum(e["location_quality"]["status"] == "suspect" for e in entries),
                "candidate_address_groups": len(candidate_groups),
                "candidate_group_records": sum(any(g["basis"] == "same_normalized_address_city_state" for g in e["duplicate_candidates"]) for e in entries),
                "candidate_identity_group_records": sum(bool(e["duplicate_candidates"]) for e in entries),
                "with_grid_binding": sum(c["grid"]["subregion"] is not None for c in cards.values()),
                "permit_documents": len(permits) + len(tx_documents), "permit_documents_parsed": va_parsed + tx_parsed,
                "virginia_permit_documents": len(permits), "virginia_permit_documents_parsed": va_parsed,
                "reviewed_permit_documents": len(recovered),
                "records_with_reviewed_permits": sum(bool(c["facility"].get("permit_reviews")) for c in cards.values()),
                "texas_permit_documents": len(tx_documents), "texas_permit_documents_parsed": tx_parsed,
                "records_with_permit_conditions": sum(bool(c["facility"]["permit_limits"]) for c in cards.values()),
                "records_with_generator_evidence": sum(any(claim["id"] == "diesel-mw" and isinstance(claim["value"], (int, float)) for claim in c["claims"]) for c in cards.values()),
                "operator_location_water_records": len((water_disclosures or {}).get("observations", [])),
                "grid_authorities": len(regions), **disclosure_coverage, **power_coverage}
    data_status["coverage"] = coverage
    validate_record_evidence(cards, entries)
    validate_disclosure_publication(disclosure_data, cards, coverage)
    files["facilities.json"] = json_bytes({"results": entries, "as_of": states["echo"].get("retrieved_at"), "release_id": release_id,
                                            "truncated": False, "source_status": sources})
    files["cards.json"] = json_bytes(cards)
    files["drawers.json"] = json_bytes(drawers)
    files["operators.json"] = json_bytes(operators)
    files["data-status.json"] = json_bytes(data_status)
    files["grid/index.json"] = json_bytes({"release_id": release_id, "as_of": built_at, "regions": grid_index})
    if geometry_data:
        geojson = {**geometry_data["geojson"], "tolerance_degrees": geometry_data.get("tolerance_degrees", 0.0001),
                   "boundary_buffer_degrees": geometry_data.get("boundary_buffer_degrees", 0.0021),
                   "overlap_geojson": geometry_data.get("overlap_geojson", {"type": "FeatureCollection", "features": []})}
        files["egrid-subregions.json"] = json_bytes(geojson)
    fallback = {"calc_version": "reference-grid-10mw-v3", "reference_mw": SCENARIO_MW, "capacity_basis": "GRID_MW",
                "release_id": release_id, "built_at": built_at, "scope": "scenario",
                "utilization": dict(zip(RANGE_KEYS, UTILIZATIONS)), "range_kind": "scenario", "state_default": {},
                "geometry": "EPA eGRID polygons; ambiguous or near-boundary locations are withheld",
                "factor_set": {"id": "epa-egrid-annual", "sha256": states["egrid"].get("result_sha256", ""), "url": results["egrid"]["url"]},
                "subregions": {}}
    for code, row in rates.items():
        energy = [SCENARIO_MW * 8760 * u for u in UTILIZATIONS]
        carbon = [x * row["co2e_rate_lb_per_mwh"] / LB_PER_TONNE for x in energy]
        fallback["subregions"][code] = {"name": row["name"], "rate_lb_per_mwh": row["co2e_rate_lb_per_mwh"],
            "badge": "ESTIMATED", "data_vintage": str(row["data_year"]), "range_kind": "scenario",
            "energy_mwh": dict(zip(RANGE_KEYS, energy)), "carbon_t": dict(zip(RANGE_KEYS, carbon)),
            "inputs": scenario_inputs(row["data_year"]),
            "source_url": row.get("source_url"), "evidence_url": citations[code]["url"],
            "observation_start": results["egrid"].get("observation_start"), "observation_end": results["egrid"].get("observation_end"),
            "published_at": results["egrid"].get("published_at"), "retrieved_at": states["egrid"].get("retrieved_at"),
            "drivers": ["10 MW illustrative grid-side preset, 45–80% utilization; no facility measurement."],
            "chain": ["10 MW grid-side reference × 8760 hours × chosen utilization", "Multiply MWh by the published annual grid rate; convert pounds to metric tonnes."],
            "citation": citations[code]}
    files["resolve-fallback.json"] = json_bytes(fallback)
    registry, explain_coverage = build_explain_registry(files, release_id=release_id, built_at=built_at)
    files["explain-registry.json"] = json_bytes(registry)
    coverage.update(explain_coverage)
    files["data-status.json"] = json_bytes(data_status)
    return files, coverage
