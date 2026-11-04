"""Reviewed operator disclosures; campus accounting is not building telemetry."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from .models import Fetcher, SourceResult
from .sources_grid import _Links, _stamp, _utc

SOURCE_ID = "meta_actuals2024"
DISCOVERY_URL = "https://sustainability.atmeta.com/"
PDF_URL = "https://sustainability.atmeta.com/asset/2025-environmental-data-index/"
PDF_SHA256 = "d7527cd23308869105b404b486117603bb3f5ee10e30499367f8698110afb66d"
PARSER_VERSION = "meta-fy2024-campus-tables-v1"
REPORT_YEAR, DATA_YEAR = 2025, 2024
PERIOD_START, PERIOD_END = "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"
LOCATION_IDS = {
    "Altoona (IA)": "meta:altoona-ia", "Clonee (Ireland)": "meta:clonee-ireland",
    "DeKalb (IL)": "meta:dekalb-il", "Eagle Mountain (UT)": "meta:eagle-mountain-ut",
    "Forest City (NC)": "meta:forest-city-nc", "Fort Worth (TX)": "meta:fort-worth-tx",
    "Gallatin (TN)": "meta:gallatin-tn", "Henrico (VA)": "meta:henrico-va",
    "Huntsville (AL)": "meta:huntsville-al", "Kansas City (MO)": "meta:kansas-city-mo",
    "Los Lunas (NM)": "meta:los-lunas-nm", "Luleå (Sweden)": "meta:lulea-sweden",
    "Mesa (AZ)": "meta:mesa-az", "New Albany (OH)": "meta:new-albany-oh",
    "Odense (Denmark)": "meta:odense-denmark", "Prineville (OR)": "meta:prineville-or",
    "Sarpy (NE)": "meta:sarpy-ne", "Stanton Springs (GA)": "meta:stanton-springs-ga",
}
COMMON_TAIL = ("Leased data center facilities", "Other data center-related facilities", "Offices total")
YEARS = "2020 2021 2022 2023 2024"
TABLES = {
    "electricity": {"page": 6, "title": "Electricity Consumption by Facility (in MWh)",
                    "overall": "Total electricity consumption", "columns": 5,
                    "metrics": (("electricity_consumption", 4, "MWh"),)},
    "water": {"page": 9, "title": "Water Withdrawal by Facility (in megaliters)",
              "overall": "Total water withdrawal", "columns": 5,
              "metrics": (("water_withdrawal", 4, "million liters"),)},
    "operational_emissions": {"page": 3, "title": "Market-Based Scope 1 and 2 Emissions (in metric tons CO2e)",
                              "overall": "Total operational GHG emissions", "columns": 5,
                              "metrics": (("emissions_scope1_and2_market", 4, "metric tonnes CO2e"),)},
    "scope2": {"page": 4, "title": "Scope 2 Emissions (in metric tons CO2e)",
               "overall": "Total facilities GHG emissions", "columns": 10,
               "metrics": (("emissions_scope2_market", 8, "metric tonnes CO2e"),
                           ("emissions_scope2_location", 9, "metric tonnes CO2e"))},
}
METRIC_NOTES = {
    "electricity_consumption": "Annual operator-reported electricity consumed; MWh is energy, not demand MW or an hourly meter series.",
    "water_withdrawal": "Annual water withdrawn from utilities/aquifers; meter or utility data where available, with proxy estimates where unavailable. This is not water consumption. Excludes construction withdrawal reported separately.",
    "emissions_scope2_market": "Operator-reported market-based Scope 2 accounting, including contractual instruments. A reported zero is not zero electricity use or zero physical grid emissions.",
    "emissions_scope2_location": "Operator-reported location-based Scope 2 accounting for purchased energy; not a measured stack total, marginal estimate or lifecycle footprint.",
    "emissions_scope1_and2_market": "Combined operational Scope 1 plus market-based Scope 2 as reported. Not Scope 1 alone, Scope 3 or an entire lifecycle footprint; no subtraction is performed to invent a separate Scope 1 observation.",
}
METHOD_NOTE = ("The operator uses activity data where available and permits estimation when activity data is incomplete. "
               "Reported values are rounded to whole units; the metered or estimated share of an individual campus row is not specified.")
SCOPE_NOTE = ("Named operator data-center campus/location as published; it may contain multiple buildings. "
              "The report provides no building-level allocation or EPA facility identity link. "
              "Independent reviewed identity evidence is required before linking this disclosure to inventory records.")


class OperatorDisclosureError(ValueError):
    pass


def _flat(text: str) -> str:
    return re.sub(r"(?<=\w)-\s+(?=\w)", "-", re.sub(r"\s+", " ", text)).strip()


def discover_meta_index(raw: bytes) -> dict:
    parser = _Links()
    parser.feed(raw.decode("utf-8-sig"))
    found = {}
    for link in parser.links:
        url = urljoin(DISCOVERY_URL, link)
        parts = urlparse(url)
        match = re.fullmatch(r"/asset/(20\d\d)-environmental-data-index/", parts.path)
        if parts.scheme == "https" and parts.netloc == "sustainability.atmeta.com" and match:
            found[int(match[1])] = url
    if not found or max(found) != REPORT_YEAR or found[REPORT_YEAR] != PDF_URL:
        raise OperatorDisclosureError("Meta's linked latest index changed or is missing; review the new edition before publication")
    return {"latest_linked_report_year": max(found), "linked_index_url": found[REPORT_YEAR],
            "discovery_url": DISCOVERY_URL, "discovery_sha256": hashlib.sha256(raw).hexdigest()}


def _consume_row(rest: str, label: str, columns: int, *, percentages=False) -> tuple[list[str], str]:
    if not rest.startswith(label + " "):
        raise OperatorDisclosureError("Meta table location order, coverage or identity changed at " + label)
    rest = rest[len(label):].lstrip()
    values = []
    for _ in range(columns):
        token, separator, rest = rest.partition(" ")
        allowed = r"(?:>99%|100%)" if percentages else r"(?:-|<\d+|\d{1,3}(?:,\d{3})+|\d+)"
        if not separator or not re.fullmatch(allowed, token):
            raise OperatorDisclosureError("Meta table has invalid, truncated or shifted numeric columns at " + label)
        values.append(token)
        rest = rest.lstrip()
    return values, rest


def parse_meta_table(text: str, table_id: str) -> dict[str, list[int]]:
    spec = TABLES[table_id]
    flat = _flat(text)
    prefix, marker, rest = flat.partition(spec["overall"] + " ")
    required = spec["title"] + " " + YEARS
    if not marker or required not in prefix or "2025 Environmental Data Index" not in prefix:
        raise OperatorDisclosureError("Meta table title, unit, report year or observation columns changed")
    if table_id == "scope2" and not prefix.endswith("Market-based Location-based " * 5):
        raise OperatorDisclosureError("Meta Scope 2 market/location column pairing changed")
    rest = spec["overall"] + " " + rest
    labels = (spec["overall"], "Data centers total", *LOCATION_IDS, *COMMON_TAIL)
    out = {}
    for label in labels:
        values, rest = _consume_row(rest, label, spec["columns"])
        # The reviewed latest-year columns must contain actual numeric disclosures.
        # Historical '-' and '<500' tokens are accepted structurally, never treated as zero.
        latest = values[-2:] if table_id == "scope2" else values[-1:]
        if any(not re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d+", value) for value in latest):
            raise OperatorDisclosureError("Meta latest-year observation is missing or qualified; do not invent zero")
        out[label] = [int(value.replace(",", "")) for value in latest]
        if table_id == "electricity" and label == spec["overall"]:
            _, rest = _consume_row(rest, "Electricity from grid (%)", 5, percentages=True)
    footer = _flat(spec["title"]) + " " + YEARS
    if table_id == "scope2":
        footer = _flat(spec["title"]) + " " + " ".join(f"{year} {basis}" for year in range(2020, 2025)
                                                          for basis in ("Market-based", "Location-based"))
        # The PDF's accessible-text duplicate footer truncates the final word;
        # the visually reviewed table header above remains authoritative.
        footer = footer.removesuffix("d")
    if table_id == "water":
        footer += " Not included in our 2024 water withdrawal numbers are an additional 1,019 mega liters of water withdrawn for the construction of Meta data centers."
    if rest != footer:
        raise OperatorDisclosureError("Meta table has additional, duplicate or unparsed rows")
    return out


def _extract_pdf(raw: bytes) -> tuple[dict, dict]:
    import pymupdf
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            if len(document) != 19 or "Reporting on fiscal year 2024" not in _flat(document[0].get_text()):
                raise OperatorDisclosureError("Meta pinned report structure or fiscal year changed")
            pages = {table_id: document[spec["page"] - 1].get_text() for table_id, spec in TABLES.items()}
            methods = " ".join(_flat(document[index].get_text()) for index in (12, 13, 14, 15, 17))
            expected = ("rounded to the nearest whole digit", "Owned, online data centers are always reported by site",
                        "operational control approach", "activity data is incomplete or unavailable",
                        "water utilities or meter data", "estimate water withdrawal and consumption",
                        "one EAC for every MWh", "1,019 mega liters")
            if not all(term in methods for term in expected):
                raise OperatorDisclosureError("Meta operational scope, rounding, estimation or market-accounting method changed")
            metadata = {"title": document.metadata.get("title"), "created": document.metadata.get("creationDate"),
                        "modified": document.metadata.get("modDate")}
    except OperatorDisclosureError:
        raise
    except Exception as exc:
        raise OperatorDisclosureError("Meta PDF could not be extracted") from exc
    return pages, metadata


def normalize_meta_actuals(raw: bytes) -> dict:
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PDF_SHA256:
        raise OperatorDisclosureError("Meta reviewed PDF bytes changed; verify scope, methods and table schema before accepting a revision")
    pages, metadata = _extract_pdf(raw)
    tables = {name: parse_meta_table(pages[name], name) for name in TABLES}
    locations = [{"disclosure_location_id": location_id, "operator": "Meta", "location_label": label,
                  "reporting_scope": "campus", "scope_note": SCOPE_NOTE,
                  "source_identifiers": {"operator_location_label": label}, "facility_binding_verified": False,
                  "source_url": PDF_URL, "publisher": "Meta", "pdf_sha256": digest}
                 for label, location_id in LOCATION_IDS.items()]
    observations, aggregates, reconciliation = [], [], []
    for table_id, spec in TABLES.items():
        rows = tables[table_id]
        for offset, (metric, _, unit) in enumerate(spec["metrics"]):
            # Scope2 is already reduced to its latest-year market/location pair;
            # the other tables have one latest-year value.
            for label, values in rows.items():
                location_id = LOCATION_IDS.get(label)
                row = {"observation_id": f"{location_id or 'meta:aggregate:' + re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}:{DATA_YEAR}:{metric}",
                       "disclosure_location_id": location_id, "operator": "Meta", "location_label": label,
                       "metric": metric, "value": values[offset], "unit": unit, "data_year": DATA_YEAR,
                       "period_start": PERIOD_START, "period_end": PERIOD_END,
                       "reporting_scope": "campus" if location_id else "operator_aggregate",
                       "reporting_basis": "operator_reported", "methodology_note": METRIC_NOTES[metric] + " " + METHOD_NOTE,
                       "scope_note": SCOPE_NOTE if location_id else "Publisher aggregate, not a single site; never allocate to facility records",
                       "source_page": spec["page"], "source_url": PDF_URL, "source_id": SOURCE_ID,
                       "pdf_sha256": digest, "source_sha256": digest, "publisher": "Meta",
                       "source_locator": {"page": spec["page"], "table": spec["title"], "row": label, "year_column": DATA_YEAR,
                                          "subcolumn": ("Market-based" if metric.endswith("market") else "Location-based") if table_id == "scope2" else None},
                       "source_native_unit": "megaliters" if metric == "water_withdrawal" else "metric tons CO2e" if metric.startswith("emissions_") else "MWh",
                       "unit_conversion": "Equivalent unit spelling only; numerical value unchanged",
                       "methodology_pages": [13, 14, 15, 16, 18], "facility_binding_verified": False,
                       "is_meter_observation": False, "metered_share_known": False,
                       "published_at": None, "report_year": REPORT_YEAR, "parser_version": PARSER_VERSION,
                       "license": "Copyright Meta; attributed factual extraction"}
                if metric.startswith("emissions_"):
                    row["gwp_basis"] = None
                    row["gwp_disclosure"] = "The reviewed report describes CO2-equivalent accounting but does not identify one GWP assessment/version for these campus rows; no EPA or Cornerstone GWP basis is substituted"
                (observations if location_id else aggregates).append(row)
            component_total = sum(rows[label][offset] for label in (*LOCATION_IDS, *COMMON_TAIL[:2]))
            data_center_total = rows["Data centers total"][offset]
            all_total = rows[spec["overall"]][offset]
            dc_difference = abs(component_total - data_center_total)
            all_difference = abs(data_center_total + rows["Offices total"][offset] - all_total)
            # 20 component rows and total rounded to the nearest whole unit.
            if dc_difference > 10.5 or all_difference > 1.5:
                raise OperatorDisclosureError("Meta named sites/other/leased/offices fail aggregate reconciliation for " + metric)
            reconciliation.append({"metric": metric, "unit": unit, "campuses_plus_leased_and_other": component_total,
                                   "published_data_center_total": data_center_total, "difference": dc_difference,
                                   "component_rounding_tolerance": 10.5, "overall_difference": all_difference,
                                   "overall_rounding_tolerance": 1.5})
    if len(locations) != 18 or len(observations) != 90 or len({x["observation_id"] for x in observations}) != 90:
        raise OperatorDisclosureError("Meta campus/metric coverage is incomplete or duplicated")
    for label in LOCATION_IDS:
        if tables["operational_emissions"][label][0] + 1 < tables["scope2"][label][0]:
            raise OperatorDisclosureError("Meta combined Scope1+2 is smaller than its Scope2 component")
    return {"locations": locations, "observations": observations, "aggregates": aggregates,
            "validation": {"status": "validated_operator_reported_campus_disclosures", "locations": 18,
                           "observation_count": 90, "metrics_per_location": 5, "aggregate_observations": len(aggregates),
                           "aggregate_reconciliation": reconciliation, "facility_bindings": 0,
                           "method_scope": "Operator-reported historical campus accounting, potentially estimated; not measured building telemetry"},
            "source": {"publisher": "Meta", "report": "2025 Environmental Data Index", "report_year": REPORT_YEAR,
                       "data_year": DATA_YEAR, "pdf_url": PDF_URL, "pdf_sha256": digest, "pdf_bytes": len(raw),
                       "pdf_metadata": metadata, "pdf_metadata_date_is_publication_date": False, "published_at": None,
                       "publication_date_precision": "report year only; exact publication date not established",
                       "parser_version": PARSER_VERSION, "license": "Copyright Meta; attributed factual extraction",
                       "source_pages": sorted(spec["page"] for spec in TABLES.values()), "methodology_pages": [13, 14, 15, 16, 18],
                       "scope_note": SCOPE_NOTE, "methodology_note": METHOD_NOTE,
                       "selection_policy": "Latest index linked from the operator homepage must match the reviewed edition and PDF hash"}}


def collect_meta_actuals(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    now = _utc(now)
    discovery = discover_meta_index(fetcher.get_bytes(DISCOVERY_URL))
    data = normalize_meta_actuals(fetcher.get_bytes(PDF_URL))
    data["source"].update(**discovery, checked_at=_stamp(now))
    return SourceResult(SOURCE_ID, "Meta 2024 reported campus electricity, water and emissions", PDF_URL,
                        data, len(data["observations"]), observation_start=PERIOD_START, observation_end=PERIOD_END,
                        published_at=None, data_vintage="2024 reporting period; Meta 2025 Environmental Data Index",
                        coverage="18 named worldwide Meta campuses, five distinct annual metrics each; no building-level allocation",
                        note=METHOD_NOTE + " " + SCOPE_NOTE + " Electricity is MWh, water is withdrawal, and emissions accounting methods remain distinct.")
