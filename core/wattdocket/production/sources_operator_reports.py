"""Pinned, attributed operator disclosures with no inferred facility joins."""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from urllib.parse import urljoin

from .models import Fetcher, SourceResult
from .sources_grid import GridSourceError, _Links, _stamp, _utc

SOURCE_ID = "google_water2025"
REPORT_PAGE = "https://sustainability.google/reports/google-2026-environmental-report/"
PDF_URL = "https://sustainability.google/files/1699a2e19277e478b38a659f00cb38398a836e8c/download-1699a2e1.pdf"
PDF_SHA256 = "6a23a6db212d5d7da8d887e773e98e78ee16b98eaf8d627a4e31f98b6f487ca3"
PARSER_VERSION = "google-water-2025-table-v1"
LOCATIONS = (
    "Ashburn, VA", "Berkeley County, SC", "Bristow, VA", "Council Bluffs, IA", "The Dalles, OR",
    "Douglas County, GA", "Dublin, Ireland", "Eemshaven, Netherlands", "Frankfurt, Germany",
    "Fredericia, Denmark", "Hamina, Finland", "Hanau, Germany", "Henderson, NV", "Inzai, Japan",
    "Jackson County, AL", "Lancaster, OH", "Leesburg, VA", "Lenoir, NC", "Lockbourne, OH",
    "Mayes County, OK", "Mesa, AZ", "Middenmeer, Netherlands", "Midlothian, TX",
    "Montgomery County, TN", "Montreal, Canada", "New Albany, OH", "New Haven, IN", "Omaha, NE",
    "Papillion, NE", "Pflugerville, TX", "Phoenix, AZ", "Quilicura, Chile", "Red Oak, TX",
    "San Bernardo, Chile", "St. Ghislain, Belgium", "Sterling, VA", "Storey County, NV",
    "Sydney, Australia", "Wilmer, TX", "Winschoten, Netherlands",
)
AGGREGATES = ("Other data center locations", "Data centers total")
COMPONENTS = {
    "Douglas County, GA": ("Potable water", "Reclaimed wastewater"),
    "Eemshaven, Netherlands": ("Potable water", "Non-potable water"),
    "St. Ghislain, Belgium": ("Potable water", "Non-potable water"),
    **{name: ("Potable water", "Non-potable water", "Reclaimed wastewater") for name in AGGREGATES},
}
FOOTNOTES = {"Dublin, Ireland": 226, "Montreal, Canada": 228, "Pflugerville, TX": 229,
             "Phoenix, AZ": 230, "San Bernardo, Chile": 231, "Storey County, NV": 233,
             "Sydney, Australia": 234, "Wilmer, TX": 235}
TABLE_HEADER = ["Water use by data center location", "2025", "Location", "Unit", "Withdrawal224",
                "Discharge", "Consumption", "Golf course", "equivalents", "(estimated)225"]
METHOD_NOTE = ("Operator-reported annual location disclosure. Withdrawal and discharge use meters or invoices where available; "
               "the report permits estimates where data is unavailable. Consumption is withdrawal minus discharge. "
               "Individual rows do not disclose their metered-versus-estimated share.")
GEOGRAPHY_NOTE = ("A named operator location may include multiple buildings. No EPA facility identifier, geographic boundary "
                  "or meter-to-facility match has been verified; these rows are not attached to facility cards.")


def _strip_footnote(text: str) -> str:
    return re.sub(r"(?:22[4-9]|23[0-5])$", "", text).strip()


def _water_number(text: str) -> float:
    if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        raise GridSourceError("Google water table has invalid or shifted numeric cells")
    value = float(text.replace(",", ""))
    if not math.isfinite(value):
        raise GridSourceError("Google water table has nonfinite values")
    return value


def parse_google_water_table(text: str) -> tuple[list[dict], list[dict], dict]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    if lines[:len(TABLE_HEADER)] != TABLE_HEADER or "Legend" not in lines:
        raise GridSourceError("Google water table year, column headings or end marker changed")
    body = lines[len(TABLE_HEADER):lines.index("Legend")]
    rows, cursor = {}, 0
    expected_names = LOCATIONS + AGGREGATES
    for expected in expected_names:
        if cursor >= len(body) or _strip_footnote(body[cursor]) != expected or expected in rows:
            raise GridSourceError("Google water table location coverage, order or identity changed")
        label = body[cursor]
        expected_label = expected + (str(FOOTNOTES[expected]) if expected in FOOTNOTES else "")
        if label != expected_label:
            raise GridSourceError("Google water location footnotes changed")
        cursor += 1
        components = COMPONENTS.get(expected, ())
        for component in components:
            if cursor >= len(body) or _strip_footnote(body[cursor]) != component:
                raise GridSourceError("Google water withdrawal component schema changed")
            cursor += 1
        if cursor >= len(body) or body[cursor] != "Million gallons":
            raise GridSourceError("Google water table units changed")
        cursor += 1
        count = 3 + len(components)
        if cursor + count >= len(body):
            raise GridSourceError("Google water table is truncated")
        values = [_water_number(value) for value in body[cursor:cursor + count]]
        cursor += count
        if body[cursor] != "<1":
            _water_number(body[cursor])  # Check, but do not publish the illustrative golf-course comparison.
        cursor += 1
        withdrawal, discharge, consumption = values[0], values[-2], values[-1]
        tolerance = 1.5 if expected == "Data centers total" else .150000001
        if abs(withdrawal - discharge - consumption) > tolerance:
            raise GridSourceError("Google water withdrawal/discharge/consumption fail the mass-balance check")
        if components and abs(sum(values[1:1 + len(components)]) - withdrawal) > tolerance:
            raise GridSourceError("Google water source components do not reconcile to withdrawal")
        rows[expected] = {
            "location_label": expected, "operator": "Google", "data_year": 2025,
            "withdrawal_million_gallons": withdrawal, "discharge_million_gallons": discharge,
            "consumption_million_gallons": consumption, "unit": "million gallons",
            "unit_note": "Publisher unit retained; no conversion to liters or a gallon convention inferred",
            "withdrawal_components_million_gallons": dict(zip(components, values[1:1 + len(components)])),
            "withdrawal_water_type": "potable unless separately itemized",
            "observation_start": "2025-01-01T00:00:00Z", "observation_end": "2026-01-01T00:00:00Z",
            "measurement_basis": "operator reported; may include estimates", "methodology_note": METHOD_NOTE,
            "facility_binding_verified": False, "geography_note": GEOGRAPHY_NOTE,
            "reporting_scope": "named operator location" if expected in LOCATIONS else "operator aggregate",
            "air_cooled_footnote": expected in FOOTNOTES,
            "footnote_ids": [224] + ([FOOTNOTES[expected]] if expected in FOOTNOTES else []) +
                            ([227] if expected == "Eemshaven, Netherlands" else []) +
                            ([232] if expected == "St. Ghislain, Belgium" else []),
            "source_id": SOURCE_ID, "source_url": PDF_URL, "source_page": 97,
            "methodology_pages": [87, 88], "footnote_page": 116,
            "limited_assurance_marked": expected != "Other data center locations",
            "assurance": ("Marked as subject to third-party limited assurance; not a claim that each value is metered"
                          if expected != "Other data center locations" else "Aggregate not marked for limited assurance in this table"),
        }
    if cursor != len(body):
        raise GridSourceError("Google water table has unparsed rows or shifted columns")
    max_total_difference = 0.0
    for field in ("withdrawal_million_gallons", "discharge_million_gallons", "consumption_million_gallons"):
        difference = abs(sum(rows[name][field] for name in LOCATIONS + (AGGREGATES[0],)) - rows[AGGREGATES[1]][field])
        # Forty named values + Other are rounded to <=0.1 million gallons;
        # the global total is rounded to whole millions: 41*.05 + .5 = 2.55.
        if difference > 2.550000001:
            raise GridSourceError("Google named locations plus Other do not reconcile to the published total")
        max_total_difference = max(max_total_difference, difference)
    return ([rows[name] for name in LOCATIONS], [rows[name] for name in AGGREGATES],
            {"status": "validated_operator_location_disclosures", "named_locations": len(LOCATIONS),
             "aggregate_rows": len(AGGREGATES), "facility_bindings": 0,
             "max_total_rounding_difference_million_gallons": max_total_difference,
             "total_rounding_tolerance_million_gallons": 2.55})


def _extract_report(raw: bytes) -> tuple[str, str, str]:
    import pymupdf
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            if len(document) != 117:
                raise GridSourceError("Pinned Google report page count changed")
            table = document[96].get_text()
            methods = document[86].get_text() + document[87].get_text()
            footnotes = document[115].get_text()
    except GridSourceError:
        raise
    except Exception as exc:
        raise GridSourceError("Google report PDF cannot be extracted") from exc
    return table, re.sub(r"\s+", " ", methods), re.sub(r"\s+", " ", footnotes)


def collect_google_water_disclosures(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    now = _utc(now)
    page = fetcher.get_bytes(REPORT_PAGE)
    parser = _Links()
    parser.feed(page.decode("utf-8-sig"))
    text = re.sub(r"\s+", " ", " ".join(parser.text_parts))
    if (PDF_URL not in {urljoin(REPORT_PAGE, url) for url in parser.links} or
            "2026 Environmental Report" not in text or "June 2026" not in text):
        raise GridSourceError("Selected Google report link/edition/publication month changed; review required")
    raw = fetcher.get_bytes(PDF_URL)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PDF_SHA256:
        raise GridSourceError("Pinned Google report bytes changed; review table, scope and methods before accepting a new edition")
    table, methods, footnotes = _extract_report(raw)
    required_methods = ("actual metered or invoiced data", "estimate water withdrawal", "estimate water discharge",
                        "subtracting water discharge from water withdrawal", "exclude seawater and brackish water")
    if not all(term in methods for term in required_methods) or "Unless otherwise specified, water withdrawals are potable water" not in footnotes:
        raise GridSourceError("Google water measurement, estimation or water-scope disclosures changed")
    observations, aggregates, validation = parse_google_water_table(table)
    for row in observations + aggregates:
        row.update(pdf_sha256=digest, parser_version=PARSER_VERSION, publisher="Google",
                   publication_month="2026-06", license="Copyright Google; attributed factual extraction")
    return SourceResult(
        source_id=SOURCE_ID, label="Google 2025 reported water by location", url=REPORT_PAGE,
        data={"observations": observations, "aggregates": aggregates, "validation": validation,
              "source": {"publisher": "Google", "report": "2026 Environmental Report", "data_year": 2025,
                         "publication_month": "2026-06", "published_at": None, "checked_at": _stamp(now),
                         "report_page": REPORT_PAGE, "pdf_url": PDF_URL, "pdf_sha256": digest,
                         "pdf_bytes": len(raw), "parser_version": PARSER_VERSION,
                         "license": "Copyright Google; attributed factual extraction", "source_page": 97,
                         "methodology_pages": [87, 88], "footnote_page": 116,
                         "scope": "40 named global operator locations; this pinned report only",
                         "selection_policy": "Pinned reviewed edition; newer editions require separate table/method review",
                         "methodology_note": METHOD_NOTE, "geography_note": GEOGRAPHY_NOTE}},
        record_count=len(observations), observation_start="2025-01-01T00:00:00Z", observation_end="2026-01-01T00:00:00Z",
        published_at=None, data_vintage="2025 annual water; Google report published June 2026 (day unspecified)",
        coverage="40 named Google locations worldwide, separately retained Other and total aggregates; zero verified facility joins",
        note=METHOD_NOTE + " " + GEOGRAPHY_NOTE + " Excludes seawater and brackish water; no 2026 actual-use claim.",
    )
