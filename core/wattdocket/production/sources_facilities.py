"""Bounded public facility inventory and Virginia permit adapters.

Retrieval/caching/archive policy belongs to the caller's Fetcher. These adapters
never write the source tree, merge facilities by name, or invent measurements.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode, urljoin, urlparse

from .models import Fetcher, SourceResult
from .identity import condition_metadata, permit_metadata, osm_coordinate_metadata

ECHO_BASE = "https://echodata.epa.gov/echo/"
ECHO_REFRESH = "https://echo.epa.gov/resources/echo-data/about-the-data"
VA_REGISTRY = ("https://www.deq.virginia.gov/news-info/shortcuts/permits/air/"
               "issued-air-permits-for-data-centers")
READER = "https://r.jina.ai/"
OSM_ENDPOINTS = ("https://overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter")
OSM_QUERY = '''[out:json][timeout:90][maxsize:67108864];
area["ISO3166-1"="US"]["admin_level"="2"]->.us;
(nwr["telecom"="data_center"](area.us);
nwr["industrial"="data_centre"](area.us);
nwr["industrial"="data_center"](area.us);
nwr["building"="data_center"](area.us);
nwr["construction:telecom"="data_center"](area.us);
nwr["proposed:telecom"="data_center"](area.us););
out center meta;'''
TEXAS_DOCUMENTS = (
    {"id": "tceq:164110:technical-review", "name": "Amazon ALE60", "permit_number": "164110",
     "document_type": "technical_review", "regulated_entity_id": "RN111192365",
     "url": "https://records.tceq.texas.gov/cs/idcplg?IdcService=TCEQ_EXTERNAL_SEARCH_GET_FILE&Rendition=Web&dID=8724316"},
    {"id": "tceq:O4679:draft-permit", "name": "Compass Red Oak Datacenter", "permit_number": "O4679",
     "document_type": "draft_permit", "regulated_entity_id": "RN111028916",
     "url": "https://records.tceq.texas.gov/cs/idcplg?IdcService=TCEQ_EXTERNAL_SEARCH_GET_FILE&Rendition=Web&dID=8935965"},
    {"id": "tceq:O4790:statement-of-basis", "name": "Vantage Data Center TX2", "permit_number": "O4790",
     "document_type": "draft_statement_of_basis", "regulated_entity_id": "RN111926465",
     "url": "https://www.tceq.texas.gov/assets/public/permitting/air/publicnotice/38600sob.pdf"},
    {"id": "tceq:O4791:statement-of-basis", "name": "Vantage TX11", "permit_number": "O4791",
     "document_type": "draft_statement_of_basis", "regulated_entity_id": None,
     "url": "https://records.tceq.texas.gov/cs/idcplg?IdcService=TCEQ_EXTERNAL_SEARCH_GET_FILE&Rendition=Web&dID=9551548"},
)


class SourceError(ValueError):
    """A source cannot be safely published as a complete current snapshot."""


def _url(service: str, **params) -> str:
    return ECHO_BASE + service + "?" + urlencode(params)


def _text(value) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def _float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _coordinates(row: dict) -> tuple[float | None, float | None]:
    lat, lon = _float(row.get("FacLat")), _float(row.get("FacLong"))
    if lat is None or lon is None:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise SourceError("Out-of-range coordinates for " + str(row.get("RegistryID")))
    return lat, lon


def _results(payload: dict) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("Results"), dict):
        raise SourceError("ECHO response has no Results object")
    result = payload["Results"]
    if result.get("Error") or "error" in str(result.get("Message", "")).lower():
        raise SourceError("ECHO query failed: " + str(result.get("Message")))
    return result


def fetch_echo_rows(fetcher: Fetcher, prefix: str, *, page_size: int = 1000,
                    max_rows: int = 100000) -> list[dict]:
    """Use one query ID and explicit columns; reject missing/repeated pages."""
    if prefix not in {"echo", "air"} or not 1 <= page_size <= 1000:
        raise ValueError("Invalid ECHO query configuration")
    service = prefix + "_rest_services."
    metadata = _results(fetcher.get_json(_url(service + "metadata", output="JSON")))
    columns = metadata.get("ResultColumns")
    if not isinstance(columns, list):
        raise SourceError("ECHO metadata lacks ResultColumns")
    column_names = {x.get("ObjectName") for x in columns}
    if not {"RegistryID", "FacLat", "FacLong"} <= column_names:
        raise SourceError("ECHO metadata lost required identity/coordinate columns")
    qcolumns = ",".join(str(x["ColumnID"]) for x in columns if x.get("ColumnID"))
    query = _results(fetcher.get_json(_url(service + "get_facilities",
                                         output="JSON", p_ncs="518210",
                                         responseset=page_size)))
    try:
        total = int(query["QueryRows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("ECHO did not declare a valid row count") from exc
    if not 1 <= total <= max_rows:
        raise SourceError(f"ECHO query count {total} outside bounds 1..{max_rows}")
    qid = query.get("QueryID")
    if not qid:
        raise SourceError("ECHO did not return a query ID")
    output, fingerprints = [], set()
    for page in range(1, math.ceil(total / page_size) + 1):
        result = _results(fetcher.get_json(_url(service + "get_qid", output="JSON",
                                               qid=qid, pageno=page, qcolumns=qcolumns)))
        if str(result.get("QueryID")) != str(qid):
            raise SourceError("ECHO changed query ID during pagination")
        if int(result.get("QueryRows", -1)) != total or int(result.get("PageNo", -1)) != page:
            raise SourceError("ECHO query count/page changed during pagination")
        rows = result.get("Facilities")
        expected = min(page_size, total - len(output))
        if not isinstance(rows, list) or len(rows) != expected:
            raise SourceError(f"ECHO page {page}: expected {expected} records")
        signature = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        if signature in fingerprints:
            raise SourceError("ECHO repeated a page")
        fingerprints.add(signature)
        output.extend(rows)
    return output


def normalize_echo(combined: list[dict], air: list[dict]) -> list[dict]:
    """One FRS facility with separate air source records; no fuzzy identity join."""
    indexed, air_by_id = {}, defaultdict(list)
    for row in combined:
        rid = _text(row.get("RegistryID"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", rid):
            raise SourceError("ECHO facility lacks a valid record identity")
        if rid in indexed:
            raise SourceError("Combined ECHO returned a duplicate RegistryID: " + rid)
        indexed[rid] = row
    source_ids = set()
    for row in air:
        rid, source = _text(row.get("RegistryID")), _text(row.get("SourceID"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", rid) or not source:
            raise SourceError("Air record lacks RegistryID or SourceID")
        if source in source_ids:
            raise SourceError("Duplicate air SourceID: " + source)
        source_ids.add(source)
        air_by_id[rid].append(row)
    status_map = {"operating": "operating", "under construction": "under_construction",
                  "planned facility": "proposed", "permanently closed": "closed",
                  "temporarily closed": "temporarily_closed", "seasonal": "seasonal"}
    output = []
    for rid in sorted(set(indexed) | set(air_by_id)):
        main = indexed.get(rid, {})
        air_rows = air_by_id.get(rid, [])
        first_air = air_rows[0] if air_rows else {}
        lat, lon = _coordinates(main)
        coordinate_source = "frs" if lat is not None else None
        if lat is None and air_rows:
            lat, lon = _coordinates(first_air)
            coordinate_source = "icis_air" if lat is not None else None
        coordinate_row = main if coordinate_source == "frs" else first_air if coordinate_source else {}
        accuracy_field = "FacAccuracyMeters" if coordinate_source == "frs" else "CalculatedAccuracyMeters"
        accuracy = _float(coordinate_row.get(accuracy_field))
        if accuracy is not None and accuracy < 0:
            accuracy = None
        raw_status = sorted({_text(r.get("AIRStatus")) for r in air_rows if r.get("AIRStatus")})
        status = status_map.get(raw_status[0].lower(), "unknown") if len(raw_status) == 1 else "unknown"
        conflicts = []
        for row in air_rows:
            a, b = _coordinates(row)
            if a is not None and lat is not None and (abs(a - lat) > .001 or abs(b - lon) > .001):
                conflicts.append({"source_id": row["SourceID"], "lat": a, "lon": b})
        output.append({
            "id": "echo:" + rid, "source_record_id": rid,
            "registry_id": rid if re.fullmatch(r"\d{12}", rid) else None,
            "identity_kind": "frs" if re.fullmatch(r"\d{12}", rid) else "program_record",
            "name": _text(main.get("FacName") or first_air.get("AIRName")),
            "address_line": _text(main.get("FacStreet") or first_air.get("AIRStreet")),
            "city": _text(main.get("FacCity") or first_air.get("AIRCity")),
            "state": _text(main.get("FacState") or first_air.get("AIRState")),
            "zip": _text(main.get("FacZip") or first_air.get("AIRZip")),
            "lat": lat, "lon": lon, "coordinate_source": coordinate_source,
            "coordinate_accuracy_m": accuracy,
            "coordinate_quality": {"accuracy_field": accuracy_field if coordinate_source else None,
                                   "reported_accuracy": coordinate_row.get(accuracy_field),
                                   "collection_method": coordinate_row.get("FacCollectionMethod" if coordinate_source == "frs" else "CollectionMethod"),
                                   "reference_point": coordinate_row.get("FacReferencePoint" if coordinate_source == "frs" else "ReferencePoint")},
            "coordinate_conflicts": conflicts, "status": status, "status_raw": raw_status,
            "status_basis": "EPA AIR program record status; not independently verified data-center operation",
            "is_operating_datacenter_verified": False,
            "naics": _text(main.get("FacNAICSCodes") or first_air.get("AIRNAICS")),
            "source_type": "epa_echo", "classification": "naics_listing",
            "class_verified": False,
            "program_ids": {kind: _text(main.get(key) or first_air.get(key)
                                        or (first_air.get("CWAIDs") if kind == "cwa" else None)) or None
                            for kind, key in {"air": "AIRIDs", "cwa": "NPDESIDs",
                                              "rcra": "RCRAIDs", "ghg": "GHGIDs",
                                              "tri": "TRIIDs", "eis": "EisIDs"}.items()},
            "air_records": air_rows, "combined_record": main,
        })
    return output


def _refresh_dates(raw: str) -> tuple[str | None, str | None]:
    # Dates belong to EPA's extract, not to this fetch. Parse the ICIS-Air row.
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", raw, re.I | re.S):
        if "ICIS-Air" in html.unescape(re.sub(r"<[^>]+>", " ", row)):
            dates = re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", row)
            if len(dates) >= 2:
                return tuple(datetime.strptime(d, "%m/%d/%Y").date().isoformat() for d in dates[-2:])
    return None, None


def collect_echo(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    combined = fetch_echo_rows(fetcher, "echo")
    air = fetch_echo_rows(fetcher, "air")
    facilities = normalize_echo(combined, air)
    refresh_error = None
    try:
        extracted, next_extract = _refresh_dates(fetcher.get_bytes(ECHO_REFRESH).decode("utf-8"))
    except Exception as exc:
        extracted, next_extract, refresh_error = None, None, str(exc)
    return SourceResult(
        "echo", "EPA ECHO facility inventory", _url("echo_rest_services.get_facilities", p_ncs="518210"),
        {"facilities": facilities, "air_records": air, "combined_records": combined,
         "publisher_extracted_at": extracted, "publisher_next_extract": next_extract,
         "refresh_metadata_error": refresh_error}, len(facilities), published_at=extracted,
        coverage=f"NAICS 518210, all ECHO programs; {len(air)} air-source records",
        note="An industry-code listing is not verification that a site is a data center. "
             "Missing coordinates are retained. Permitted capacity is not electricity use.")


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.links = [], None, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell, self.links = [], []
        elif tag == "a" and self.cell is not None:
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append((_text("".join(self.cell)), self.links))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def _issued_iso(raw: str) -> str | None:
    try:
        return datetime.strptime(raw.replace("-", "/"), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parse_virginia_registry(raw: str) -> tuple[list[dict], str | None]:
    """Count all linked permit rows; markup quirks must never silently drop one."""
    rows = []
    if "<table" in raw.lower():
        parser = _TableParser()
        parser.feed(raw)
        candidates = parser.rows
    else:
        candidates = []
        for line in raw.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = []
            for cell in line.strip().strip("|").split("|"):
                links = re.findall(r"\]\((https?://[^)]+)\)", cell)
                # Keep adjacent link text adjacent: [74063-](url)[5](url).
                plain = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", cell).replace("*", "")
                cells.append((_text(plain), links))
            candidates.append(cells)
    count, seen = 0, set()
    for cells in candidates:
        permit_links = [urljoin(VA_REGISTRY, u) for _, links in cells for u in links
                        if "showpublisheddocument" in u.lower()]
        if not permit_links:
            continue
        count += 1
        if len(cells) < 6:
            raise SourceError("Virginia linked permit row has fewer than six columns")
        name, reg, issued, program, county, office = [v[0] for v in cells[:6]]
        reg = re.sub(r"\s+", "", reg)
        if not re.fullmatch(r"\d{4,6}-\d+", reg) or not name:
            raise SourceError("Unrecognized Virginia registration/name: " + repr(reg))
        urls = set(permit_links)
        if len(urls) != 1:
            raise SourceError("Virginia permit row links multiple documents: " + reg)
        url = urls.pop()
        if urlparse(url).hostname not in {"www.deq.virginia.gov", "deq.virginia.gov"}:
            raise SourceError("Virginia registry links an unexpected document host")
        if reg in seen:
            raise SourceError("Virginia registry repeats registration: " + reg)
        seen.add(reg)
        record = {"site": name, "registration": reg, "issued": issued,
                  "issued_iso": _issued_iso(issued), "program": program,
                  "county": county, "office": office, "permit_url": url}
        rows.append({**record, **permit_metadata(record)})
    if not rows or len(rows) != count:
        raise SourceError("Virginia registry has no complete permit rows")
    # A publisher date is not manufactured if the heading changes.
    plain = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    match = re.search(r"Issued Air Permits for Data Centers\s+as of\s+"
                      r"([A-Za-z.]+\s+\d{1,2},?\s+\d{4})", plain, re.I)
    stamp = None
    if match:
        date = match.group(1).replace(".", "").replace(",", "")
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                stamp = datetime.strptime(date, fmt).date().isoformat()
                break
            except ValueError:
                pass
    return rows, stamp


def _reader_pages(raw: str) -> dict[int, str]:
    """Require consecutive printed headers around a page of intermediary text.

    Cover letters and permit bodies can restart at page 2. Text before a first
    header, across a reset, or after the last header cannot be safely bounded.
    The page number belongs to the FOLLOWING body: confirmed against the
    publisher's 74190-2 and 30142-3 PDFs, not inferred from the legacy parser.
    """
    body = raw.split("Markdown Content:", 1)[-1]
    pattern = re.compile(r"\bPage\s+(\d{1,3})\b")
    markers = list(pattern.finditer(body))
    pages, ambiguous = {}, set()
    for current, following in zip(markers, markers[1:]):
        number, next_number = int(current.group(1)), int(following.group(1))
        content = _text(body[current.end():following.start()])
        if content and next_number == number + 1:
            if number in pages and pages[number] != content:
                ambiguous.add(number)
            else:
                pages[number] = content
    # Unbounded final text is retained in raw evidence, not assigned a guessed page.
    return {number: text for number, text in pages.items() if number not in ambiguous}


def extract_virginia_document(raw: bytes, publisher_url: str, *, retrieved_via: str,
                              registration: str) -> dict:
    from wattdocket.va_permits import PermitDocument, _OBLIGATIONS, _condition_window, _EQUIP_ROW

    digest = hashlib.sha256(raw).hexdigest()
    direct_pdf = raw.startswith(b"%PDF-")
    if direct_pdf:
        try:
            import pymupdf
        except ImportError as exc:
            raise SourceError("PyMuPDF is needed to extract publisher PDF bytes") from exc
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            pages = {i + 1: _text(page.get_text()) for i, page in enumerate(pdf)
                     if _text(page.get_text())}
            claimed = len(pdf)
        representation = "publisher_pdf"
    else:
        text = raw.decode("utf-8", errors="strict")
        if "access denied" in text.lower()[:500] or "<html" in text.lower()[:500]:
            raise SourceError("Permit response is an HTML error page")
        pages = _reader_pages(text)
        number = re.search(r"Number of Pages:\s*(\d+)", text)
        claimed = int(number.group(1)) if number else None
        representation = "reader_text"
    if not pages or sum(map(len, pages.values())) < 100:
        raise SourceError("Permit has no verifiable paginated text (scanned or unpaginated)")
    doc = PermitDocument(url=publisher_url, title="Virginia air permit " + registration,
                         page_count_claimed=claimed, pages=pages,
                         retrieved_via=retrieved_via, registration=registration.split("-")[0])
    # Keep every distinct condition. A campus can have multiple engine groups
    # with different limits; selecting the first number would hide that scope.
    obligations, seen_obligations = [], set()
    for spec in _OBLIGATIONS:
        for number, page in pages.items():
            for match in re.finditer(spec["pattern"], page, re.I):
                value = float(match.group(spec["value_group"]).replace(",", ""))
                quote = _condition_window(page, match.start(), match.end())
                key = spec["metric"], value, quote
                if key in seen_obligations:
                    continue
                seen_obligations.add(key)
                obligations.append({"metric": spec["metric"], "value": value, "unit": spec["unit"],
                                    "scope": "quoted_condition", "applies_to_entire_site": False,
                                    "citation": {**doc.cite(quote), "page": number}})
    derivation = "PDF page index" if direct_pdf else "document's printed page header; bounded by next consecutive header"

    def evidence(citation: dict) -> dict:
        page, quote = citation.get("page"), citation.get("quote")
        if page not in pages or not quote or _text(quote) not in pages[page]:
            raise SourceError("Permit quotation failed independent page verification")
        return {**citation, "content_sha256": digest, "representation": representation,
                "page_derivation": derivation, "retrieved_via": retrieved_via,
                **({"document_sha256": digest} if direct_pdf else {})}

    for item in obligations:
        item["citation"] = evidence(item["citation"])
        item.update(condition_metadata(item))
    equipment = []
    for number, page in pages.items():
        items = []
        for match in _EQUIP_ROW.finditer(page):
            count, kw = int(match.group(1)), float(match.group("ekw").replace(",", ""))
            if 1 <= count <= 500 and 50 <= kw <= 20000:
                quote = match.group(0)
                citation = evidence({**doc.cite(quote), "page": number})
                items.append({"count": count, "rated_ekw_each": kw, "citation": citation})
        if items:
            # Do not aggregate separate pages/campuses into an asserted site total.
            total = sum(x["count"] * x["rated_ekw_each"] for x in items)
            equipment.append({"metric": "generator_units", "value": sum(x["count"] for x in items),
                              "unit": "generators in cited equipment table", "rated_ekw": total,
                              "mw_total": total / 1000, "scope": "equipment_table_page",
                              "is_complete_fleet": False, "page": number, "rows": len(items),
                              "citations": [x["citation"] for x in items], "equipment_rows": items})
    return {"status": "parsed", "content_sha256": digest, "representation": representation,
            "publisher_url": publisher_url, "retrieved_via": retrieved_via,
            "pages_claimed": claimed, "pages_recovered": len(pages),
            "page_numbers": sorted(pages), "obligations": obligations, "equipment": equipment,
            "extraction_version": "va-deterministic-3"}


def collect_virginia(fetcher: Fetcher, *, now: datetime,
                     fetch_documents: bool = True) -> SourceResult:
    errors = []
    try:
        raw = fetcher.get_bytes(VA_REGISTRY)
        permits, stamp = parse_virginia_registry(raw.decode("utf-8"))
        registry_via = VA_REGISTRY
    except Exception as exc:
        errors.append("Publisher registry: " + str(exc))
        raw = fetcher.get_bytes(READER + VA_REGISTRY)
        permits, stamp = parse_virginia_registry(raw.decode("utf-8"))
        registry_via = READER
    # Versioned registry URLs allow bounded reuse of an already verified exact
    # document. Historical repository captures keep their unknown retrieval
    # date; archive age is a cache policy, not a claim of a new source check.
    previous_docs = {}
    if getattr(fetcher, "store", None) is not None:
        from .storage import read_json
        previous = read_json(fetcher.store, "state/sources/virginia_permits.json", {})
        previous_docs = {(p.get("registration"), p.get("permit_url")): p.get("document") or {}
                         for p in previous.get("result", {}).get("data", {}).get("permits", [])}
    def read_document(permit):
        url = permit["permit_url"]
        if not fetch_documents:
            permit["document"] = {"status": "not_fetched", "obligations": [], "equipment": []}
            return permit
        old = previous_docs.get((permit["registration"], url), {})
        basis = "retrieved_at" if old.get("retrieved_at") else "archived_by_at"
        cache_date = old.get(basis)
        if old.get("status") == "parsed" and old.get("extraction_version") == "va-deterministic-3" and cache_date:
            from .fetch import parsed, stamp as utc_stamp
            try:
                age = (now - parsed(cache_date)).total_seconds()
            except (ValueError, TypeError):
                age = float("inf")
            if 0 <= age < getattr(fetcher, "document_max_age", 30 * 86400):
                permit["document"] = {**old, "cache_reused": True, "cache_age_basis": basis,
                                      "freshness": "archived" if basis == "archived_by_at" else "cached",
                                      "latest_fetch_status": "not_attempted_cached",
                                      "registry_link_checked_at": utc_stamp(now)}
                return permit
        attempts = []
        for via, target in ((url, url), (READER, READER + url)):
            try:
                content = fetcher.get_bytes(target)
                if via != READER and not content.startswith(b"%PDF-"):
                    raise SourceError("Publisher document did not return PDF bytes")
                permit["document"] = extract_virginia_document(
                    content, url, retrieved_via=via, registration=permit["registration"])
                permit["document"]["retrieval_warnings"] = attempts
                receipt = getattr(fetcher, "receipt", lambda _: None)(target)
                if receipt:
                    permit["document"]["receipt"] = receipt
                    permit["document"]["retrieved_at"] = receipt.get("retrieved_at")
                break
            except Exception as exc:
                attempts.append(str(exc))
        else:
            permit["document"] = {"status": "failed", "publisher_url": url,
                                  "error": "; ".join(attempts), "obligations": [], "equipment": []}
        return permit
    # Four bounded requests at a time, deterministic result order. The fetcher
    # caches content-addressed documents and preserves the original receipt date.
    with ThreadPoolExecutor(max_workers=4) as pool:
        permits = list(pool.map(read_document, permits))
    for permit in permits:
        permit.update(permit_metadata(permit))
        for condition in permit["document"].get("obligations", []):
            condition.update(condition_metadata(condition))
    ok = sum(p["document"]["status"] == "parsed" for p in permits)
    failed = sum(p["document"]["status"] == "failed" for p in permits)
    cached = sum(bool(p["document"].get("cache_reused")) for p in permits)
    return SourceResult(
        "virginia_permits", "Virginia DEQ data-center air permits", VA_REGISTRY,
        {"registry_as_of": stamp, "registry_sha256": hashlib.sha256(raw).hexdigest(),
         "registry_retrieved_via": registry_via, "registry_warnings": errors,
         "registry_receipt": getattr(fetcher, "receipt", lambda _: None)(
             VA_REGISTRY if registry_via == VA_REGISTRY else READER + VA_REGISTRY),
         "permits": permits, "documents_ok": ok, "documents_failed": failed,
         "documents_cached": cached,
         "documents_not_fetched": len(permits) - ok - failed}, len(permits), published_at=stamp,
        coverage=f"{len(permits)} issued-permit registry rows; {ok} documents parsed; {failed} failed",
        note="The registry lists air permits for data centers; linking an EPA record requires verified regulator identity. "
             "Text retrieved through a reader is identified separately from original PDF bytes. "
             "Equipment extracted from a page is not asserted to be the complete fleet.")


def normalize_osm(payload: dict) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise SourceError("Overpass did not return elements")
    if payload.get("remark"):
        raise SourceError("Overpass partial/error response: " + str(payload["remark"]))
    if not payload.get("osm3s", {}).get("timestamp_osm_base"):
        raise SourceError("Overpass has no database timestamp")
    output, seen = [], set()
    for element in payload["elements"]:
        kind, ident = element.get("type"), element.get("id")
        if kind not in {"node", "way", "relation"} or not isinstance(ident, int):
            raise SourceError("Unexpected Overpass element identity")
        key = f"osm:{kind}:{ident}"
        if key in seen:
            raise SourceError("Duplicate Overpass element: " + key)
        seen.add(key)
        tags = element.get("tags", {})
        point = element if kind == "node" else element.get("center", {})
        lat, lon = _float(point.get("lat")), _float(point.get("lon"))
        if lat is not None and not (-90 <= lat <= 90 and lon is not None and -180 <= lon <= 180):
            raise SourceError("Out-of-range Overpass coordinates")
        status = "unknown"
        if tags.get("proposed:telecom") == "data_center":
            status = "proposed"
        elif tags.get("construction:telecom") == "data_center":
            status = "under_construction"
        output.append({"id": key, "osm_type": kind, "osm_id": ident,
                       **osm_coordinate_metadata(kind),
                       "name": tags.get("name") or tags.get("operator") or "Unnamed mapped data center",
                       "operator": tags.get("operator"), "lat": lat, "lon": lon,
                       "address_line": _text(" ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")]))),
                       "city": tags.get("addr:city"), "state": tags.get("addr:state"),
                       "zip": tags.get("addr:postcode"), "status": status,
                       "updated_at": element.get("timestamp"), "version": element.get("version"),
                       "source_type": "openstreetmap", "classification": "community_mapped",
                       "class_verified": False, "tags": tags,
                       "url": f"https://www.openstreetmap.org/{kind}/{ident}",
                       "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors"})
    if not output:
        raise SourceError("Overpass unexpectedly returned no US data-center elements")
    return output


def collect_osm(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    failures = []
    for endpoint in OSM_ENDPOINTS:
        url = endpoint + "?" + urlencode({"data": OSM_QUERY})
        try:
            payload = fetcher.get_json(url)
            facilities = normalize_osm(payload)
            return SourceResult("osm", "OpenStreetMap community data-center inventory", url,
                                {"facilities": facilities, "query": OSM_QUERY,
                                 "license": "ODbL-1.0", "license_url": "https://www.openstreetmap.org/copyright",
                                 "attribution": "© OpenStreetMap contributors",
                                 "timestamp_osm_base": payload["osm3s"]["timestamp_osm_base"]},
                                len(facilities), published_at=payload["osm3s"]["timestamp_osm_base"],
                                coverage="US tagged nodes, buildings and campuses; may overlap",
                                note="© OpenStreetMap contributors; ODbL 1.0 (https://www.openstreetmap.org/copyright). "
                                     "Community mappings are not regulator verification. Elements are not merged "
                                     "with EPA sites or with each other without separately verified identity links.")
        except Exception as exc:
            failures.append(endpoint + ": " + str(exc))
    raise SourceError("; ".join(failures))


def collect_texas(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    """Recheck four explicitly selected publisher documents, preserving draft state.

    This is not a state inventory or a permit-status discovery feed. No fact in
    these documents is asserted to be a currently effective operating limit.
    """
    from wattdocket.permits import (PermitDocument, extract_claims, extract_equipment,
                                   detect_value_conflicts, facility_identity)

    records = []
    for selected in TEXAS_DOCUMENTS:
        item = {**selected, "is_current_effective_limit": False}
        try:
            raw = fetcher.get_bytes(selected["url"])
            if not raw.startswith(b"%PDF-"):
                raise SourceError("TCEQ publisher did not return PDF bytes")
            with TemporaryDirectory(prefix="wattdocket-tceq-") as folder:
                path = Path(folder) / (selected["permit_number"] + ".pdf")
                path.write_bytes(raw)
                doc = PermitDocument.load(path)
                if sum(map(len, doc.pages)) < 100:
                    raise SourceError("TCEQ PDF has no readable text layer")
                claims, equipment = extract_claims(doc), extract_equipment(doc)
                conflicts = detect_value_conflicts(doc)
                # Add the publisher URL to every nested citation, validating the
                # quote against the exact fetched PDF before publication.
                def enrich(value):
                    if isinstance(value, dict):
                        if "quote" in value and "page" in value:
                            page = int(value["page"])
                            if not 1 <= page <= len(doc.pages) or _text(value["quote"]) not in doc.pages[page - 1]:
                                raise SourceError("TCEQ citation failed page verification")
                            value.update(url=selected["url"], document_type=selected["document_type"],
                                         is_current_effective_limit=False)
                        for child in list(value.values()):
                            enrich(child)
                    elif isinstance(value, list):
                        for child in value:
                            enrich(child)
                for value in (claims, equipment, conflicts):
                    enrich(value)
                prepared = re.search(r"Prepared on:\s*([A-Za-z]+ \d{1,2}, \d{4})", " ".join(doc.pages[:2]))
                date = datetime.strptime(prepared.group(1), "%B %d, %Y").date().isoformat() if prepared else None
                item.update(status="parsed", content_sha256=doc.sha256, pages=doc.page_count,
                            document_date=date, representation="publisher_pdf",
                            identity=facility_identity(doc), claims=claims, equipment=equipment,
                            conflicts=conflicts, receipt=getattr(fetcher, "receipt", lambda _: None)(selected["url"]))
        except Exception as exc:
            item.update(status="failed", error=str(exc), claims=[], equipment=[])
        records.append(item)
    ok = sum(x["status"] == "parsed" for x in records)
    if not ok:
        raise SourceError("All selected Texas document fetches/extractions failed: " +
                          "; ".join(x["error"] for x in records))
    dates = [x["document_date"] for x in records if x.get("document_date")]
    return SourceResult("texas_permits", "Selected Texas air-permit documents",
                        "https://www.tceq.texas.gov/permitting/air/air_status_permits.html",
                        {"documents": records, "documents_ok": ok, "documents_failed": len(records) - ok,
                         "selection": "four historically selected documents; not a Texas inventory",
                         "latest_effective_status_verified": False}, len(records),
                        observation_start=min(dates) if dates else None,
                        observation_end=max(dates) if dates else None,
                        coverage=f"{ok}/{len(records)} selected documents parsed",
                        note="Drafts and technical reviews retain their document type. "
                             "A fresh download does not prove the document is the latest effective permit.")
