"""Release-scoped, cited inputs for explanations; no model-facing live lookups.

Only finished public objects are accepted. Their own receipt and scope gates
remain mandatory here: a human-readable REPORTED/verified label is insufficient.
Power reporting sites deliberately remain separate from inventory records.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit

SCHEMA_VERSION = "1"
MAX_FACTS = 12
MAX_FACT_TEXT = 900
_HASH = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_METRICS = {
    "electricity_consumption": "electricity consumption",
    "water_withdrawal": "water withdrawal",
    "water_consumption": "water consumption",
    "water_discharge": "water discharge",
    "water_use_reported": "reported water use",
    "emissions_scope1": "Scope 1 emissions",
    "emissions_scope2_location": "location-based Scope 2 emissions",
    "emissions_scope2_market": "market-based Scope 2 emissions",
    "emissions_scope1_and2_market": "combined Scope 1 and market-based Scope 2 emissions",
    "reported_ghg_total": "reported greenhouse-gas total",
}


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _display(value):
    result = format(Decimal(str(value)), ",f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _quantity(value, unit, label):
    return {"value": value, "unit": unit, "label": label,
            "display": str(value) if unit == "year" else _display(value)}


def _rounded_quantity(value, unit, label, places=0):
    rounded = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return {"value": value, "unit": unit, "label": label, "display": f"{rounded:,.{places}f}", "rounded": True}


def _key(prefix, value):
    return prefix + "-" + hashlib.sha256(str(value).encode()).hexdigest()[:20]


def _https(url):
    return isinstance(url, str) and urlsplit(url).scheme == "https" and bool(urlsplit(url).netloc)


def _locator(value):
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items() if v is not None)
    return str(value or "published source record")


def _receipt(item, *, url_key="source_url", hash_key="source_sha256", receipt_key="source_receipt"):
    """Match original request URL OR its documented redirect, plus exact bytes."""
    url, digest, receipt = item.get(url_key), item.get(hash_key), item.get(receipt_key) or {}
    if not (_https(url) and isinstance(digest, str) and _HASH.fullmatch(digest)):
        return None
    if receipt.get("sha256") != digest or url not in (receipt.get("url"), receipt.get("final_url")):
        return None
    if not receipt.get("retrieved_at") or not _number(receipt.get("bytes")) or receipt["bytes"] <= 0:
        return None
    return {"url": url, "sha256": digest,
            "locator": _locator(item.get("source_locator") or item.get("source_page") or item.get("source_field") or item.get("locator")),
            "publisher": item.get("publisher") or ("DOE/NREL/ALLIANCE" if item.get("source_id") == "nrel_esif_power" else "Published source"),
            "retrieved_at": receipt["retrieved_at"], "representation": item.get("representation"),
            "freshness": item.get("source_status") or item.get("collection_status")}


def _fact(fid, text, kind, scope, evidence, *, period=None, numbers=(), qualifiers=(), selector=None):
    if len(text) > MAX_FACT_TEXT or not evidence:
        return None
    if not _ID.fullmatch(fid) or any(q.casefold() not in text.casefold() for q in qualifiers):
        raise ValueError("Invalid explanation fact identity or qualifier")
    if any(n["display"] not in text for n in numbers):
        raise ValueError("Explanation quantity missing from its sentence")
    return {"id": fid, "text": text, "kind": kind, "scope": scope, "period": period,
            "numbers": list(numbers), "evidence": evidence, "required_qualifiers": list(qualifiers),
            "source_selector": selector}


def _period(item, year_key="data_year"):
    year = item.get(year_key)
    if not isinstance(year, int) or isinstance(year, bool):
        return None
    start, end = item.get("period_start"), item.get("period_end")
    if start != f"{year}-01-01T00:00:00Z" or end != f"{year+1}-01-01T00:00:00Z":
        return None
    return {"start": start, "end": end, "year": year, "end_exclusive": True}


def _annual_fact(location, observation):
    metric, value = observation.get("metric"), observation.get("value")
    evidence, period = _receipt(observation), _period(observation)
    if (not evidence or not period or metric not in _METRICS or not _number(value) or value < 0
            or observation.get("disclosure_location_id") != location["disclosure_location_id"]
            or observation.get("inventory_allocation") is not False
            or observation.get("value_origin") != "reported_disclosure"):
        return None
    scope, sid = observation.get("reporting_scope"), location.get("source_id")
    boundaries = {"campus": "campus total", "operator_location": "operator-location total",
                  "building": "whole-property total", "regulatory_facility": "regulatory reporting entity"}
    if scope not in boundaries or not isinstance(observation.get("unit"), str):
        return None
    boundary, year = boundaries[scope], period["year"]
    qualifiers = [str(year), boundary]
    label = _METRICS[metric]
    if sid in {"massachusetts_energy", "nyc_benchmarking"} and metric == "electricity_consumption":
        label = "purchased-grid electricity consumption"
    # Water withdrawal/consumption/discharge share a unit, as do distinct GHG
    # scopes. Unit/number matching alone cannot prevent swapping their meaning.
    qualifiers.append(label)
    tail = ""
    if scope == "building":
        tail += " This is not data-center-only use and is not allocated to any inventory record."
        qualifiers.append("not data-center-only")
    elif scope in {"campus", "operator_location"}:
        tail += " It is not allocated to individual buildings or inventory records."
        qualifiers.append("not allocated")
    if metric == "electricity_consumption":
        tail += " Annual energy is not peak demand."
        qualifiers.append("not peak demand")
    if sid == "ghgrp":
        tail += " This covers direct stationary combustion under Subpart C, excluding electricity Scope 2 and biogenic CO2; it is not a complete facility footprint."
        qualifiers.extend(["Subpart C", "excluding electricity Scope 2"])
    elif metric == "reported_ghg_total":
        tail += " These emissions are publisher-calculated from reported energy, not directly measured emissions or a complete Scope 1/2 inventory."
        qualifiers.append("publisher-calculated")
    basis = observation.get("reporting_basis", "")
    if "estimat" in basis.lower() or sid == "google_water2025":
        tail += " The report may include estimates."
        qualifiers.append("may include estimates")
    if observation.get("source_status") in {"stale", "failed"}:
        tail += " The last successful source capture is stale."
        qualifiers.append("stale")
    text = (f"For {year}, {location['location_label']} reports {label} of {_display(value)} {observation['unit']} "
            f"as a {boundary}.{tail} Source: {evidence['publisher']}.")
    return _fact(_key("annual", observation["observation_id"]), text, "annual_disclosure", scope,
                 [evidence], period=period,
                 numbers=[_quantity(value, observation["unit"], metric), _quantity(year, "year", "reporting year")],
                 qualifiers=qualifiers, selector={"path": "facility-observations.json", "disclosure_location_id": location["disclosure_location_id"],
                                                 "observation_id": observation["observation_id"]})


def _permit_evidence(permit):
    # Archived reader text is valid last-known quoted evidence, not a fresh PDF
    # receipt; preserve its representation and never invent a retrieval date.
    if not (_https(permit.get("url")) and _HASH.fullmatch(str(permit.get("content_sha256", "")))
            and permit.get("parse_status") == "parsed"
            and (permit.get("extraction_version") == "va-deterministic-3" or _review_matches(permit))
            and permit.get("is_document_condition") is True and permit.get("quote")
            and isinstance(permit.get("page"), int) and permit["page"] > 0
            and (permit.get("retrieved_at") or permit.get("archived_by_at"))):
        return None
    return {"url": permit["url"], "sha256": permit["content_sha256"],
            "locator": f"Permit {permit['registration']}, page {permit['page']}",
            "publisher": "Virginia DEQ", "quote": permit["quote"],
            **{k: permit.get(k) for k in ("representation", "page_derivation", "retrieved_at", "archived_by_at", "freshness", "latest_fetch_status")}}


def _review_matches(permit):
    review = permit.get("recovery_review") or {}
    return (permit.get("extraction_version") == "va-reviewed-pdf-1"
            and review.get("status") == "verified" and review.get("review_version") == "va-reviewed-pdf-1"
            and review.get("document_sha256") == permit.get("content_sha256")
            and review.get("registration") == permit.get("registration")
            and review.get("publisher_url") == permit.get("url")
            and review.get("supersession_review", {}).get("current_legal_effectiveness") == "unknown")


def _review_scope(review):
    extent = review.get("document_extent")
    if extent == "partial_amendment":
        return " This is a partial amendment, not a complete permit.", ["partial amendment", "not a complete permit"]
    if extent in {"correspondence", "permit_letter"}:
        return " This is permit correspondence, not a complete permit.", ["permit correspondence", "not a complete permit"]
    return "", []


def _permit_review_fact(facility, permit, *, standalone=False):
    if not _review_matches(permit) or (not standalone and not _permit_identity(facility, permit)):
        return None
    review = permit["recovery_review"]
    suffix, scope_qualifiers = _review_scope(review)
    text = (("This separate permit document was checked against the regulator's registration and county; no facility measurement or inventory allocation is inferred. " if standalone else
             "The recovered original PDF was checked against the regulator's registration and county for this inventory record. ") +
            "Its presence in the issued-permit index confirms a listed document; current effectiveness is unknown."
            + suffix + " It does not establish actual equipment operation or a complete physical-site footprint. Source: Virginia DEQ.")
    evidence = [{"url": permit["url"], "sha256": permit["content_sha256"], "publisher": "Virginia DEQ",
                 "locator": "Reviewed original PDF " + permit["registration"], "representation": permit.get("representation"),
                 "retrieved_at": permit.get("retrieved_at")}]
    return _fact(_key("permit-review", permit["registration"]), text, "permit_document_review", "document_review", evidence,
                 period={"issued_at": permit.get("issued_at"), "current_effectiveness_verified": False},
                 qualifiers=["current effectiveness is unknown", *scope_qualifiers],
                 selector={**({"path": "data-status.json"} if standalone else {"path": "cards.json", "facility_id": facility["id"]}), "registration": permit["registration"],
                           "content_sha256": permit["content_sha256"]})



def _permit_context_facts(facility, permit, *, standalone=False):
    if not standalone and not _permit_identity(facility, permit): return []
    facts=[]
    selector={"path":"data-status.json"} if standalone else {"path":"cards.json","facility_id":facility["id"]}
    selector.update(registration=permit["registration"],content_sha256=permit["content_sha256"])
    site=permit.get("site_identity_review") or {}
    if site.get("status")=="regulator_site_verified":
        address=site.get("physical_address") or {}; status=site.get("site_status") or {}
        capture_date=str(site["retrieved_at"]).split("T",1)[0]
        text=("The reviewed Virginia DEQ record identifies the regulatory air site at " + ", ".join(str(address.get(k)) for k in ("street","city","state","postal_code") if address.get(k))
            + ". In the source captured on " + capture_date + ", the publisher reports the air-site status as " + str(status.get("air_operating_status") or "unknown")
            + "; site status is not permit legal effectiveness or measured operation. This is a regulatory-site identity, not a whole-campus or meter boundary. Source: Virginia DEQ.")
        qualifiers=[capture_date,"regulatory-site identity","not permit legal effectiveness","not a whole-campus or meter boundary"]
        if site.get("source_freshness")=="stale":
            text += " This is a last-known site record; refresh delayed."
            qualifiers.append("last-known site record; refresh delayed")
        facts.append(_fact(_key("permit-site",permit["registration"]),text,"regulator_site_identity","regulatory_air_site",site["evidence"],
            qualifiers=qualifiers, selector={**selector,"field":"site_identity_review"}))
    history=permit.get("permit_history_review") or {}
    if history:
        legal=history.get("legal_effectiveness",{})
        summary=history.get("note","")
        text=("The permit-history review checked the listed document chain. " + summary
              + " Current legal effectiveness is unknown; latest listing or site activity does not establish an active permit.")
        if history.get("status")!="verified": text += " A changed or unavailable source requires a new review."
        # Keep a readable, bounded summary; full events remain next to cited evidence.
        if len(text)>MAX_FACT_TEXT:
            raise ValueError("Permit-history summary needs a concise reviewed version; do not truncate scope qualifiers")
        evidence=[e for event in history.get("events",[]) for e in event.get("evidence",[])]
        if not evidence:
            receipt=history.get("registry_listing",{}).get("receipt") or {}
            if receipt: evidence=[{"url":receipt.get("url"),"sha256":receipt.get("sha256"),"receipt":receipt,"locator":"Issued permit index, registration "+permit["registration"]}]
        if evidence and legal.get("status")=="unknown":
            qualifiers=["Current legal effectiveness is unknown"]
            for phrase in ("not final issuance","pending","not an issued replacement","requires a new review"):
                if phrase in text: qualifiers.append(phrase)
            facts.append(_fact(_key("permit-history",permit["registration"]),text,"permit_history_review","document_history",evidence,
                qualifiers=qualifiers,selector={**selector,"field":"permit_history_review"}))
    return facts

def _permit_identity(facility, permit):
    identity = facility.get("identity_match") or {}
    if identity.get("status") == "matched" and identity.get("method") == "reviewed_deq_icis_cross_reference":
        root = str(permit.get("registration", "")).split("-")[0]
        county = str(permit.get("county_fips", ""))
        return any(link.get("record_id") == facility.get("id") and link.get("verification_status") == "verified"
            and link.get("relationship") == "same_regulator_record"
            and root in re.findall(r"\d+", str(link.get("expected_record", {}).get("air_record", {}).get("OtherPermitIDs", "")))
            and str(link.get("expected_record", {}).get("air_record", {}).get("AIRFIPSCode")) == "51" + county
            for link in identity.get("reviewed_links", []))
    if identity.get("status") != "matched" or identity.get("method") != "county_and_registration":
        return False
    county, registration = str(permit.get("county_fips", "")), str(permit.get("registration", "")).split("-")[0]
    expected = "VA00000051" + county + registration
    return bool(re.fullmatch(r"\d{3}", county) and registration.isdigit()
                and expected in identity.get("matched_source_ids", []))


def _permit_fact(facility, permit, *, standalone=False):
    evidence = _permit_evidence(permit)
    value, metric = permit.get("value"), permit.get("metric")
    labels = {"mcrt_hours_limit": "scheduled maintenance and readiness-testing condition",
              "generator_hours_limit": "all-purpose operating-hours condition",
              "generator_group_hours_limit": "combined generator-group operating-hours condition",
              "fuel_throughput_limit": "diesel-fuel throughput condition"}
    if (not evidence or not (_review_matches(permit) if standalone else _permit_identity(facility, permit)) or metric not in labels
            or not _number(value) or value < 0 or permit.get("applies_to_entire_site") is not False):
        return None
    date = permit.get("issued_at") or "date not established"
    freshness = "archived " if permit.get("freshness") == "archived" else ""
    scope = "only the equipment and operating purpose named in the quotation"
    individual = "for each named engine, " if re.search(r"each individual|each engine", permit["quote"], re.I) and metric != "fuel_throughput_limit" else ""
    purpose = permit.get("reviewed_operating_purpose") or labels[metric]
    text = (f"The {freshness}Virginia DEQ permit issued {date} quotes the {purpose} of "
            f"{_display(value)} {permit['unit']} {individual}covering {scope}, using the period stated in the quotation. "
            f"This is a document condition, not actual use; current effectiveness is unknown. "
            f"Source: Virginia DEQ, {permit.get('program', 'air permit')}, page {permit['page']}.")
    qualifiers = [purpose, "document condition", "not actual use", "current effectiveness is unknown", "only the equipment"]
    if permit.get("issued_at"):
        qualifiers.append(permit["issued_at"])
    if individual:
        qualifiers.append("each named engine")
    if freshness:
        qualifiers.append("archived")
    if permit.get("freshness") == "stale":
        text += " The latest document refresh failed; this is stale evidence."
        qualifiers.append("stale")
    if permit.get("extraction_version") == "va-reviewed-pdf-1":
        if permit.get("page") not in permit.get("recovery_review", {}).get("eligible_condition_pages", []):
            return None
        suffix, extra = _review_scope(permit["recovery_review"])
        text += suffix
        qualifiers += extra
        for key in ("reviewed_condition_scope", "reviewed_period"):
            if isinstance(permit.get(key), str) and permit[key]:
                text += " " + permit[key].rstrip(".") + "."
                qualifiers.append(permit[key].rstrip("."))
        if permit.get("representation") == "reviewed_pdf_transcription":
            text += " The quoted value comes from a visually checked transcription of the original PDF."
            qualifiers.append("visually checked transcription")
    return _fact(_key("permit", f"{permit['registration']}:{metric}:{permit['content_sha256']}:{value}"),
                 text, "permit_condition", "quoted_condition", [evidence],
                 period={"issued_at": permit.get("issued_at"), "current_effectiveness_verified": False},
                 numbers=[_quantity(value, permit["unit"], metric)], qualifiers=qualifiers,
                 selector={**({"path": "data-status.json"} if standalone else {"path": "cards.json", "facility_id": facility["id"]}), "registration": permit["registration"],
                           "metric": metric, "content_sha256": permit["content_sha256"], "value": value})


def _equipment_fact(facility, claim, *, standalone=False):
    citations, meta = claim.get("citations", []), claim.get("permit_metadata") or {}
    if (claim.get("evidence_scope") != "equipment_table_page" or claim.get("is_complete_fleet") is not False
            or claim.get("unit") != "MW nameplate" or not _number(claim.get("value"))
            or (not standalone and not _permit_identity(facility, meta)) or not citations):
        return None
    evidence = []
    for c in citations:
        reviewed = meta.get("recovery_review") or {}
        reviewed_ok = (c.get("verifier_version") == "va-reviewed-pdf-1"
                       and reviewed.get("status") == "verified" and reviewed.get("document_sha256") == c.get("doc_sha256")
                       and reviewed.get("registration") == meta.get("registration")
                       and reviewed.get("publisher_url") == c.get("url")
                       and c.get("page") in reviewed.get("eligible_equipment_pages", []))
        if standalone and not reviewed_ok:
            return None
        if not (c.get("verified") is True and (c.get("verifier_version") == "va-deterministic-3" or reviewed_ok)
                and c.get("verification_scope") == "quoted_text_and_locator; legal_effectiveness_not_verified"
                and _https(c.get("url")) and _HASH.fullmatch(str(c.get("doc_sha256", "")))
                and c.get("quote") and isinstance(c.get("page"), int)
                and (c.get("retrieved_at") or c.get("archived_by_at"))):
            return None
        evidence.append({"url": c["url"], "sha256": c["doc_sha256"], "publisher": "Virginia DEQ",
                         "locator": f"{c.get('dataset', 'Equipment table')}, page {c['page']}",
                         **{k: c.get(k) for k in ("quote", "representation", "retrieved_at", "archived_by_at", "freshness")}})
    text = (f"The cited equipment table in the Virginia DEQ permit issued {claim.get('as_of')} sums to "
            f"{_display(claim['value'])} MW nameplate for listed generators. This is not a whole-site total "
            f"or actual operating use; current effectiveness is unknown. Source: Virginia DEQ, cited equipment table.")
    qualifiers = ["equipment table", "MW nameplate", "not a whole-site total", "current effectiveness is unknown"]
    if claim.get("as_of"):
        qualifiers.append(claim["as_of"])
    if claim.get("freshness") in {"archived", "stale"}:
        text += f" The document evidence is {claim['freshness']}."
        qualifiers.append(claim["freshness"])
    suffix, extra = _review_scope(meta.get("recovery_review") or {})
    text += suffix
    qualifiers += extra
    if claim.get("reviewed_table_scope"):
        text += " " + claim["reviewed_table_scope"].rstrip(".") + "."
        qualifiers.append(claim["reviewed_table_scope"].rstrip("."))
    return _fact("generator-nameplate", text, "equipment_nameplate", "equipment_table_page", evidence,
                 period={"issued_at": claim.get("as_of"), "current_effectiveness_verified": False},
                 numbers=[_quantity(claim["value"], "MW nameplate", "generator table capacity")], qualifiers=qualifiers,
                 selector={**({"path": "data-status.json", "registration": meta["registration"]} if standalone else
                              {"path": "cards.json", "facility_id": facility["id"]}), "claim_id": claim["id"]})


def _scenario_fact(card, files, release_id):
    claim = next((c for c in card.get("claims", []) if c.get("id") == "scenario-carbon"), {})
    inputs, citations = claim.get("inputs") or {}, claim.get("citations") or []
    if (claim.get("scope") != "scenario" or inputs.get("facility_measurement") is not False
            or inputs.get("capacity_basis") != "GRID_MW" or not claim.get("calc_id")
            or card.get("facility", {}).get("location_quality", {}).get("map_eligible") is not True
            or not citations or not _number(claim.get("value"))):
        return None
    c = citations[0]
    prefix = f"/data/releases/{release_id}/"
    path = c.get("url", "")
    if not path.startswith(prefix):
        return None
    raw = files.get(path[len(prefix):])
    if raw is None or hashlib.sha256(raw).hexdigest() != c.get("doc_sha256") or c.get("verified") is not True:
        return None
    year, load = inputs.get("factor_year"), inputs.get("load_mw")
    util = (inputs.get("utilization") or {}).get("base")
    if not isinstance(year, int) or not _number(load) or not _number(util) or not 0 <= util <= 1:
        return None
    row = json.loads(raw)
    rate, hours = row.get("co2e_rate_lb_per_mwh"), inputs.get("hours_per_year")
    if not _number(rate) or not _number(hours) or row.get("data_year") != year:
        return None
    energy = load * util * hours
    if not math.isclose(energy * rate / 2204.622621848776, claim["value"], abs_tol=.001):
        return None
    total = _rounded_quantity(claim["value"], "t CO2e/yr", "scenario emissions")
    factor = _rounded_quantity(rate, "lb CO2e/MWh", "historical regional factor", 1)
    text = (f"A hypothetical {_display(load)} MW grid-side load at {_display(util*100)}% utilization produces "
            f"{_display(energy)} MWh/year and about {total['display']} t CO2e/yr using the {year} EPA regional annual factor "
            f"of about {factor['display']} lb CO2e/MWh. "
            "This is not a facility measurement or a current hourly emissions rate. Source: EPA eGRID normalized workbook row.")
    evidence = {"url": "https://wattdocket.com" + path, "sha256": c["doc_sha256"],
                "publisher": "U.S. EPA eGRID / WattDocket calculation", "locator": c.get("dataset", "normalized workbook row"),
                "publisher_url": c.get("publisher_url"), "retrieved_at": c.get("retrieved_at")}
    return _fact("reference-scenario", text, "scenario", "hypothetical_grid_side_load", [evidence],
                 period={"start": claim.get("observation_start"), "end": claim.get("observation_end"),
                         "year": year, "scope": "factor period, not facility observation"},
                 numbers=[_quantity(load, "MW", "hypothetical grid-side load"), _quantity(util*100, "%", "chosen utilization"),
                          _quantity(energy, "MWh/year", "hypothetical annual electricity"), total, factor,
                          _quantity(year, "year", "factor year")],
                 qualifiers=["hypothetical", str(year), "not a facility measurement"],
                 selector={"path": "cards.json", "facility_id": card["facility"]["id"], "claim_id": "scenario-carbon",
                           "factor_path": path[len(prefix):]})


def _demand_fact(site, item):
    evidence, period = _receipt(item), _period(item, "reporting_year")
    if (not evidence or not period or item.get("metric") != "electricity_peak_demand_reported"
            or item.get("is_meter_observation") is not False or not _number(item.get("value")) or item["value"] <= 0
            or item.get("unit") not in {"kW", "MW"} or item.get("site_id") != site["site_id"]):
        return None
    year, value = period["year"], item["value"]
    boundary = item.get("measurement_boundary")
    if boundary == "reported_campus_maximum" and item.get("reporting_scope") == "campus" and item.get("whole_facility") is True:
        text = (f"For {year}, {site['name']} reports a campus maximum electricity demand of {_display(value)} {item['unit']}. "
                "This is an operator-reported annual peak, not an hourly meter series; the integration interval is unknown. "
                "It is not allocated to an inventory record or individual building.")
        qualifiers = [str(year), "campus maximum", "operator-reported", "interval is unknown"]
    elif boundary == "largest_individual_meter_entry" and item.get("whole_facility") is False:
        month = item.get("peak_month")
        text = (f"For {year}, {site['name']} reports {_display(value)} {item['unit']} as the largest individual meter entry "
                f"for peak electricity demand (peak month: {month}). It is not a whole-property peak or an hourly series; "
                "the integration interval is unknown.")
        qualifiers = [str(year), "largest individual meter entry", "not a whole-property peak", "interval is unknown"]
    else:
        return None
    if site.get("facility_type") == "cryptocurrency_mining" or "MARA" in site.get("name", ""):
        text += " The reporting site is cryptocurrency mining, using ASIC equipment."
        qualifiers.append("cryptocurrency mining")
    text += f" Source: {evidence['publisher']}, {_locator(item.get('source_locator'))}."
    return _fact(_key("demand", item["demand_id"]), text, "reported_peak_demand", item["reporting_scope"], [evidence],
                 period=period, numbers=[_quantity(value, item["unit"], "reported peak demand"), _quantity(year, "year", "reporting year")],
                 qualifiers=qualifiers, selector={"path": "facility-power.json", "site_id": site["site_id"], "demand_id": item["demand_id"]})


def _series_facts(site, series):
    evidence = _receipt(series)
    if (not evidence or series.get("historical_snapshot") is not True or series.get("unit") != "kW"
            or series.get("site_id") != site["site_id"] or series.get("whole_facility") is not False
            or not _number(series.get("maximum_reported_kw"))):
        return []
    start, end = series.get("observed_start", ""), series.get("observed_end", "")
    basis = series.get("measurement_basis")
    if basis == "derived_hourly_arithmetic_mean_of_metered_samples":
        boundary, method = "IT equipment", "qualified hourly arithmetic means of meter samples"
        tail = " Cooling and other building loads are excluded; this is not a billing peak."
        qualifiers = ["historical", "IT equipment", "hourly arithmetic means", "not a billing peak"]
    elif basis == "publisher_reported_meter_power":
        boundary, method = "publisher-defined HPC meter", "publisher-reported hourly power values"
        tail = " Whether values are hourly averages or instantaneous samples is unspecified; whole-facility coverage is unverified."
        qualifiers = ["historical", "HPC meter", "whole-facility coverage is unverified"]
    else:
        return []
    maximum = _rounded_quantity(series["maximum_reported_kw"], "kW", "maximum retained series value", 1)
    text = (f"{site['name']} has historical {method} for its {boundary} from {start} to {end} "
            f"(UTC, end exclusive). The largest retained value is about {maximum['display']} kW.{tail} "
            f"This is not live telemetry. Source: {evidence['publisher']}.")
    period = {"start": series["observed_start"], "end": series["observed_end"], "end_exclusive": True}
    main = _fact(_key("series", series["series_id"]), text, "historical_power_series", boundary, [evidence],
                 period=period, numbers=[maximum], qualifiers=qualifiers + [start, end, "not live telemetry"],
                 selector={"path": "facility-power.json", "site_id": site["site_id"], "series_id": series["series_id"]})
    counts = [series.get(k) for k in ("measured_count", "missing_count")]
    completeness = None
    if all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts):
        text = (f"The historical series retains {_display(counts[0])} numeric hourly values and {_display(counts[1])} missing or withheld hours. "
                f"Missing values remain unknown, not zero; no gap is filled. Source: {evidence['publisher']} and WattDocket quality checks.")
        completeness = _fact(_key("coverage", series["series_id"]), text, "series_completeness", boundary, [evidence], period=period,
                             numbers=[_quantity(counts[0], "hours", "numeric hours"), _quantity(counts[1], "hours", "missing hours")],
                             qualifiers=["historical", "unknown, not zero"],
                             selector={"path": "facility-power.json", "site_id": site["site_id"], "series_id": series["series_id"]})
    return [f for f in (main, completeness) if f]


def build_explain_registry(files: dict[str, bytes], *, release_id: str, built_at: str) -> tuple[dict, dict]:
    """Return (public registry object, coverage). Caller serializes and hashes it.

    `files` contains final *uncompressed* release JSON bytes. No HTTP requests,
    mutable current pointers, private credentials, or cross-release joins occur.
    """
    if not _ID.fullmatch(release_id):
        raise ValueError("Unsafe release identity")
    cards = json.loads(files["cards.json"])
    payloads = {}
    for path in ("facility-observations.json", "facility-power.json", "data-status.json"):
        if path in files:
            item = json.loads(files[path])
            if item.get("release_id") != release_id or item.get("built_at") != built_at:
                raise ValueError("Explanation inputs must belong to the same release")
            payloads[path] = item
    subjects, aliases, skipped = {}, {}, Counter()
    snapshot_hashes = {path: hashlib.sha256(files[path]).hexdigest() for path in
                       ("cards.json", "facility-observations.json", "facility-power.json") if path in files}

    def snapshot(path, locator):
        return {"url": f"https://wattdocket.com/data/releases/{release_id}/{path}",
                "sha256": snapshot_hashes[path], "locator": locator,
                "publisher": "WattDocket published release", "representation": "release_json"}

    def add(kind, ident, title, facts):
        if not _ID.fullmatch(ident):
            raise ValueError("Unsafe explanation subject identity")
        key = f"{kind}:{ident}"
        if key in subjects:
            raise ValueError("Duplicate explanation subject")
        unique = {}
        for fact in facts:
            if fact:
                unique.setdefault(fact["id"], fact)
        chosen = list(unique.values())[:MAX_FACTS]
        if not chosen:
            raise ValueError("Explanation subject has no supported facts")
        fallback = chosen[:5]
        subjects[key] = {"kind": kind, "id": ident, "title": title, "facts": chosen,
                         "fallback": {"fact_ids": [f["id"] for f in fallback], "text": " ".join(f["text"] for f in fallback)}}
        return key

    locations = {}
    annual_facts = {}
    for location in payloads.get("facility-observations.json", {}).get("locations", []):
        ident = location["disclosure_location_id"]
        if ident in locations:
            raise ValueError("Duplicate disclosure location")
        locations[ident] = location
        facts = []
        # Newest reporting year first, electricity before water before emissions.
        ordered = sorted(location.get("observations", []), key=lambda o: (-o.get("data_year", 0),
                         list(_METRICS).index(o["metric"]) if o.get("metric") in _METRICS else 999, o.get("observation_id", "")))
        for observation in ordered:
            fact = _annual_fact(location, observation)
            if fact:
                facts.append(fact)
            else:
                skipped["annual_observations_without_eligible_evidence"] += 1
        annual_facts[ident] = facts
        if not facts:
            facts = [_fact("reporting-gap", "This reporting location has no eligible numeric annual observation in this release; missing values are unknown, not zero. Source: WattDocket published disclosure record.",
                           "data_gap", location.get("reporting_scope", "reporting_location"), [snapshot("facility-observations.json", ident)], qualifiers=["unknown, not zero"])]
        add("disclosure_location", ident, location["location_label"], facts)

    for slug, card in sorted(cards.items()):
        if card.get("release_id") != release_id or card.get("data_status", {}).get("built_at") != built_at:
            raise ValueError("Explanation card belongs to a different release")
        facility = card["facility"]
        ident, facts = facility["id"], []
        for related in card.get("disclosures", []):
            link = related.get("link") or {}
            lid = link.get("disclosure_location_id")
            if (link.get("verification_status") != "verified" or link.get("record_id") != ident
                    or link.get("relationship") != "within_disclosure_location"
                    or link.get("inventory_allocation") is not False or link.get("allocation_allowed") is not False
                    or lid not in locations or link not in locations[lid].get("record_links", [])):
                skipped["unverified_disclosure_links"] += 1
                continue
            proof = [_receipt(e, url_key="url", hash_key="content_sha256", receipt_key="receipt")
                     for e in link.get("evidence", []) if e.get("verified") is True]
            if not proof or any(e is None for e in proof) or len(proof) != len(link.get("evidence", [])):
                skipped["unverified_disclosure_links"] += 1
                continue
            for original in annual_facts[lid][:5]:
                f = copy.deepcopy(original)
                f["kind"] = "linked_annual_disclosure"
                f["text"] = "Related reporting location: " + f["text"] + " This total is not allocated to this inventory record."
                f["required_qualifiers"] += ["Related reporting location", "not allocated to this inventory record"]
                f["evidence"] += proof
                f["related_subject"] = "disclosure_location:" + lid
                if len(f["text"]) <= MAX_FACT_TEXT:
                    facts.append(f)
        permit_facts = [_permit_fact(facility, p) for p in sorted(facility.get("permit_limits", []),
                        key=lambda p: (p.get("issued_at") or "", p.get("registration") or ""), reverse=True)]
        facts += [f for f in permit_facts if f][:6]
        facts += [f for p in facility.get("permit_reviews", []) for f in _permit_context_facts(facility, p)][:2]
        facts += [f for p in facility.get("permit_reviews", []) if (f := _permit_review_fact(facility, p))][:2]
        for claim in card.get("claims", []):
            if claim.get("id") == "diesel-mw":
                f = _equipment_fact(facility, claim)
                if f:
                    facts.append(f)
        # Reserve room for explicit scenario and gap scope even on rich records.
        facts = facts[:MAX_FACTS-2]
        scenario = _scenario_fact(card, files, release_id)
        if scenario:
            facts.append(scenario)
        expected_missing = {"power-mw", "facility-carbon", "water-gal"}
        actual_claims = [c for c in card.get("claims", []) if c.get("id") in expected_missing]
        if ({c["id"] for c in actual_claims} != expected_missing or len(actual_claims) != len(expected_missing)
                or any(c.get("scope") != "facility" or c.get("value") not in (None, "NOT_PUBLISHED") for c in actual_claims)):
            # Future whole-facility observations need an explicit scoped fact
            # adapter before publication; never keep yesterday's absence claim.
            raise ValueError("Whole-facility measurement claims require explicit explanation support; cannot assert a gap")
        gap = ("This inventory record has no assigned, verified whole-facility electricity demand, water-use or emissions measurement. "
               "Related reporting-location totals and separate power sites retain their own boundaries; missing values are unknown, not zero. "
               "Source: WattDocket published card.")
        facts.append(_fact("record-measurement-gap", gap, "data_gap", "inventory_record", [snapshot("cards.json", slug)],
                           qualifiers=["whole-facility", "unknown, not zero"]))
        key = add("facility", ident, facility["name"], facts)
        for alias in (slug, ident):
            if alias in aliases and aliases[alias] != key:
                raise ValueError("Ambiguous explanation alias")
            aliases[alias] = key

    power = payloads.get("facility-power.json", {})
    for site in power.get("sites", []):
        if site.get("inventory_record_ids") != []:
            raise ValueError("Power sites must remain unjoined in the explanation registry")
        proofs = [_receipt(e, url_key="url", hash_key="sha256", receipt_key="receipt") for e in site.get("identity_evidence", [])]
        facts = []
        if proofs and all(proofs):
            facts = [_demand_fact(site, d) for d in power.get("demands", []) if d.get("site_id") == site["site_id"]]
            for series in power.get("series", []):
                if series.get("site_id") == site["site_id"]:
                    facts.extend(_series_facts(site, series))
        else:
            skipped["power_sites_without_identity_receipts"] += 1
        evidence = [snapshot("facility-power.json", site["site_id"])]
        facts.append(_fact("power-identity-boundary", "This power-reporting site remains a separate reporting entity. Its quantities are not assigned to an EPA inventory record or allocated to other buildings. Source: WattDocket published power-site record.",
                           "identity_boundary", "power_reporting_site", evidence, qualifiers=["separate reporting entity", "not assigned"]))
        add("power_site", site["site_id"], site["name"], facts)
    for permit in payloads.get("data-status.json", {}).get("permit_recovery", {}).get("documents", []):
        if not _review_matches(permit):
            raise ValueError("Independent permit subject lacks its exact reviewed PDF")
        ident = permit["registration"]
        context = {"id": ident}
        facts = _permit_context_facts(context, permit, standalone=True)
        facts += [_permit_review_fact(context, permit, standalone=True)]
        facts += [_permit_fact(context, item, standalone=True) for item in permit.get("conditions", [])]
        facts += [_equipment_fact(context, claim, standalone=True) for claim in permit.get("equipment_claims", [])]
        add("permit_document", ident, (permit.get("site") or "Virginia DEQ permit") + " · " + ident, facts)
    counts = Counter(s["kind"] for s in subjects.values())
    coverage = {"explain_subjects": len(subjects), "explain_facts": sum(len(s["facts"]) for s in subjects.values()),
                "explain_subject_kinds": dict(counts), "explain_withheld": dict(skipped)}
    result = {"schema_version": SCHEMA_VERSION, "release_id": release_id, "built_at": built_at,
              "subjects": dict(sorted(subjects.items())), "aliases": dict(sorted(aliases.items())), "coverage": coverage}
    return result, coverage
