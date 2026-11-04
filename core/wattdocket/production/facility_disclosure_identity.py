"""Reviewed disclosure-to-record coverage, never a name/city join or allocation.

Primary evidence establishes the named operator location and a regulated
record's presence there. It does not establish identical reporting boundaries.
Every current rule is consequently a coverage link with allocation disabled.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser

from .models import Fetcher, SourceResult
from .sources_grid import _stamp, _utc

SOURCE_ID = "disclosure_identity"
REVIEW_VERSION = "disclosure-coverage-2026-11-03-v1"
MAX_DOCUMENT_BYTES = 16_000_000


class DisclosureIdentityError(ValueError):
    pass


# Short, reviewed passages rather than fuzzy text classification. Publication
# dates are supplied only when established by the source; retrieval is separate.
EVIDENCE = (
    {"id": "google-georgia", "url": "https://datacenters.google/locations/georgia/",
     "publisher": "Google", "kind": "html", "published_at": None,
     "excerpts": ["Google builds and opens our data center in Douglas County."]},
    {"id": "google-ga-water", "url": "https://epd.georgia.gov/document/publication/discharge-permit-public-notice-1082015pdf/download",
     "publisher": "Georgia Environmental Protection Division", "kind": "pdf", "published_at": None,
     "document_date": None, "page": 1,
     "expected_sha256": "8b00e8dc3d20aed24e1e7a3058d58d134f9f56e21d00077dbae4c764fe12cfee",
     "excerpts": ["DOUGLAS COUNTY Google Inc., 300 Riverside Parkway S.W., Lithia Springs, Georgia 30122, NPDES Permit No. GA0038920"]},
    {"id": "meta-henrico", "url": "https://datacenters.atmeta.com/2020/08/henrico-county-we-are-online/",
     "publisher": "Meta", "kind": "html", "published_at": "2020-08-05",
     "excerpts": ["our Henrico Data Center in Virginia is now serving traffic", "The 2.5 million-square-foot campus"]},
    {"id": "henrico-acfr", "url": "https://henrico.gov/assets/Henrico-County-VA-FY25-ACFR.pdf",
     "publisher": "Henrico County, Virginia", "kind": "pdf", "published_at": None,
     "document_period_end": "2025-06-30", "page": 152,
     "expected_sha256": "f8fdf0934166d48484c4263201716c25696d1ea2d52122e4ecfd0e54bdb09dc1",
     "excerpts": ["Scout Development LLC, and Facebook Inc., (collectively, “Facebook”)", "facility located at 6200/7301 Technology Boulevard"]},
    {"id": "nc-labor-andale", "url": "https://www.labor.communications.its.state.nc.us/elevPublic/scripts/e_pa_1d.cfm?state_id=27994",
     "publisher": "North Carolina Department of Labor", "kind": "html", "published_at": None,
     "excerpts": ["Owner (86303) FACEBOOK 284 SOCIAL CIRCLE FOREST CITY NC 28043", "Occupant (86304) ANDALE DATA CENTER 284 SOCIAL CIRCLE"]},
    {"id": "meta-forest", "url": "https://datacenters.atmeta.com/2012/04/facebook-data-center-opens-in-forest-city-n-c/",
     "publisher": "Meta", "kind": "html", "published_at": "2012-04-19",
     "excerpts": ["Our new data center in Forest City, North Carolina", "The data center currently comprises two buildings"]},
    {"id": "oregon-vitesse", "url": "https://data.oregon.gov/resource/dws8-8evh.json?$q=Vitesse",
     "publisher": "Oregon Department of Revenue", "kind": "json", "published_at": None,
     "document_period_end": "2022-06-30",
     "row_selector": {"fiscal_year": "2022", "business_firm_name": "Vitesse, LLC (Facebook)",
                      "facility_location_address": "735 SW Connect Way", "facility_location_city": "Prineville OR"},
     "excerpts": ["Vitesse, LLC (Facebook); 735 SW Connect Way; Prineville OR"]},
    {"id": "meta-prineville", "url": "https://datacenters.atmeta.com/2021/03/facebooks-prineville-data-center-is-growing-again/",
     "publisher": "Meta", "kind": "html", "published_at": "2021-03-18",
     "excerpts": ["Our Prineville Data Center is growing again!", "11-building campus"]},
    {"id": "billerica-tax", "url": "https://billerica.gov/DocumentCenter/View/12799/FY2025-PP-Tax-Commitment-Book",
     "publisher": "Town of Billerica", "kind": "pdf", "published_at": None,
     "document_date": "2024-12-19", "page": 66,
     "expected_sha256": "6962fa2b4cce89fcd1d1df00971e65cc66114951338f8e66d73ff7c955aeaf13",
     "excerpts": ["EQUINIX, LLC", "LOC: 41 ALEXANDER RD", "CURR OWNER: 755525 - EQUINIX, LLC"]},
    {"id": "ma-billerica", "url": "https://services1.arcgis.com/7iJyYTjCtKsZS1LR/arcgis/rest/services/2026_10_28_LBER_Annual_Report_Data/FeatureServer/0/query?where=UBID%3D%2787JCHQ2M%2B7VV-32-27-31-23%27&outFields=UBID%2CStreet_Address%2CCity%2CGross_Floor_Area%2CProperty_Type&returnGeometry=false&f=json",
     "publisher": "Massachusetts Department of Energy Resources", "kind": "arcgis", "published_at": None,
     "row_selector": {"UBID": "87JCHQ2M+7VV-32-27-31-23", "Street_Address": "41 Alexander Rd", "City": "Billerica", "Gross_Floor_Area": 110162},
     "display_fields": ["UBID", "Street_Address", "City", "Gross_Floor_Area"],
     "excerpts": ["87JCHQ2M+7VV-32-27-31-23; 41 Alexander Rd; Billerica; 110162"]},
)


def _rule(location, operator, label, record_id, name, address, city, state, evidence, *, program=None, reporting_scope=None, note=None):
    return {
        "disclosure_location_id": location, "operator": operator, "location_label": label,
        "reporting_scope": reporting_scope or ("campus" if operator == "Meta" else "operator_location"),
        "record_id": record_id, "relationship": "within_disclosure_location",
        "allocation_allowed": False, "same_physical_scope_verified": False,
        "coverage_is_exhaustive": False, "evidence_ids": list(evidence),
        "expected_record": {"id": record_id, "registry_id": record_id.split(":", 1)[1],
                            "name": name, "address": address, "city": city, "state": state,
                            "program_id": program},
        "note": note or "This record is associated with the disclosed operator location. The disclosure may cover multiple "
                "buildings or registrations. Keep the total once at the disclosure location; do not allocate it to this record.",
    }


REVIEWED_LINKS = (
    _rule("google:douglas-county-ga", "Google", "Douglas County, GA", "echo:110032592727", "GOOGLE INC. - IDI",
          "300 RIVERSIDE PARKWAY", "LITHIA SPRINGS", "GA", ("google-georgia", "google-ga-water")),
    _rule("google:douglas-county-ga", "Google", "Douglas County, GA", "echo:110061650100", "GOOGLE, LLC",
          "300 RIVERSIDE PARKWAY", "LITHIA SPRINGS", "GA", ("google-georgia", "google-ga-water"), program="GA0038920"),
    _rule("meta:henrico-va", "Meta", "Henrico (VA)", "echo:110070072383", "SCOUT DEVELOPMENT LLC",
          "6200 TECHNOLOGY BLVD", "SANDSTON", "VA", ("meta-henrico", "henrico-acfr")),
    _rule("meta:forest-city-nc", "Meta", "Forest City (NC)", "echo:110043697416", "ANDALE, INC.",
          "284 SOCIAL CIRCLE", "FOREST CITY", "NC", ("meta-forest", "nc-labor-andale")),
    _rule("meta:prineville-or", "Meta", "Prineville (OR)", "echo:110071163188", "VITESSE, LLC",
          "735 SW CONNECT WAY", "PRINEVILLE", "OR", ("meta-prineville", "oregon-vitesse")),
    _rule("ma-lber:87jchq2m-7vv-32-27-31-23", "Equinix", "41 Alexander Rd, Billerica", "echo:110070822658", "EQUINIX",
          "41 ALEXANDER RD", "BILLERICA", "MA", ("billerica-tax", "ma-billerica"), reporting_scope="building",
          note="The town places Equinix at the state's disclosed building address. The state's whole-building reporting "
               "boundary is not proven equal to the EPA registration or Equinix colocation/meter boundary. "
               "Keep the building total once on its UBID disclosure; do not allocate it to tenants or source records."),
)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def _space(value):
    return re.sub(r"\s+", " ", value).strip()


def _evidence_text(spec, raw):
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise DisclosureIdentityError("Identity evidence has invalid size")
    if spec["kind"] == "pdf":
        import pymupdf
        if not raw.startswith(b"%PDF-"):
            raise DisclosureIdentityError("Identity PDF source returned another format")
        with pymupdf.open(stream=raw, filetype="pdf") as document:
            if not 1 <= spec["page"] <= len(document) <= 300:
                raise DisclosureIdentityError("Identity PDF page coverage changed")
            return _space(document[spec["page"] - 1].get_text())
    if spec["kind"] in ("json", "arcgis"):
        rows = json.loads(raw)
        if spec["kind"] == "arcgis":
            if not isinstance(rows, dict) or rows.get("error") or rows.get("exceededTransferLimit"):
                raise DisclosureIdentityError("Identity ArcGIS response failed or was truncated")
            rows = [item.get("attributes", {}) for item in rows.get("features", [])]
        if not isinstance(rows, list):
            raise DisclosureIdentityError("Identity source is not a row array")
        selected = [row for row in rows if all(row.get(k) == v for k, v in spec["row_selector"].items())]
        if len(selected) != 1:
            raise DisclosureIdentityError("Reviewed identity row missing or duplicated")
        # These fields are explicitly physical facility fields, never the
        # separate company_mailing_address in Oregon's record.
        fields = spec.get("display_fields") or ("business_firm_name", "facility_location_address", "facility_location_city")
        return "; ".join(str(selected[0][k]) for k in fields)
    parser = _VisibleText()
    parser.feed(raw.decode("utf-8-sig"))
    return _space(" ".join(parser.parts))


def collect_disclosure_identity(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    """Capture bounded primary sources. Missing/changed evidence fails closed.

    CaptureFetcher archives bytes and preserves cached original retrieval dates.
    This adapter supplies narrow verified passages and their original page.
    The builder must still call resolve_disclosure_links for current inventory.
    """
    checked = _stamp(_utc(now))
    evidence = []
    for spec in EVIDENCE:
        raw = fetcher.get_bytes(spec["url"])
        sha = hashlib.sha256(raw).hexdigest()
        if spec.get("expected_sha256") and sha != spec["expected_sha256"]:
            raise DisclosureIdentityError(f"Reviewed identity document changed: {spec['id']}")
        text = _evidence_text(spec, raw)
        if not all(_space(excerpt).casefold() in text.casefold() for excerpt in spec["excerpts"]):
            raise DisclosureIdentityError(f"Reviewed identity passage missing: {spec['id']}")
        if spec["kind"] == "html" and spec.get("published_at") and spec["published_at"] not in raw.decode("utf-8-sig"):
            raise DisclosureIdentityError(f"Reviewed publication date missing: {spec['id']}")
        receipt_method = getattr(fetcher, "receipt", None)
        receipt = receipt_method(spec["url"]) if receipt_method else None
        receipt = receipt or getattr(fetcher, "receipts", {}).get(spec["url"])
        if (not receipt or receipt.get("sha256") != sha or not receipt.get("retrieved_at") or
                receipt.get("raw_key") != f"raw/{sha}.gz"):
            raise DisclosureIdentityError("Identity evidence requires its matching immutable capture receipt")
        evidence.append({
            **{k: spec[k] for k in ("id", "url", "publisher", "published_at", "document_date", "document_period_end", "page") if k in spec},
            "excerpts": spec["excerpts"], "content_sha256": sha, "verified": True,
            "representation": {"html": "publisher_html", "pdf": "publisher_pdf", "json": "publisher_json", "arcgis": "publisher_json"}[spec["kind"]],
            "retrieved_at": receipt["retrieved_at"],
            "checked_at": checked, "receipt": receipt,
        })
    links = [{**copy.deepcopy(rule), "review_version": REVIEW_VERSION, "reviewed_at": "2026-11-03",
              "checked_at": checked, "verification_status": "pending_inventory_check"} for rule in REVIEWED_LINKS]
    return SourceResult(
        source_id=SOURCE_ID, label="Reviewed operator-disclosure identity evidence",
        url="https://wattdocket.com/app/#/data", record_count=len(links),
        data={"links": links, "evidence": evidence, "review_version": REVIEW_VERSION,
              "validation": {"primary_documents": len(evidence), "reviewed_links": len(links),
                             "disclosure_locations": len({x["disclosure_location_id"] for x in links}),
                             "exact_scope_links": 0, "numeric_allocations": 0}},
        data_vintage="Reviewed physical-location evidence; original document dates retained",
        coverage="Five disclosure locations linked to six registry records by reviewed primary evidence; no numeric allocation",
        note="A coverage relationship is not equality of reporting boundaries. Inventory changes require re-review; "
             "unreviewed names and cities never create a link.",
    )


def _words(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _address(value):
    aliases = {"street": "st", "road": "rd", "boulevard": "blvd", "parkway": "pkwy",
               "lane": "ln", "avenue": "ave", "drive": "dr", "circle": "cir"}
    return " ".join(aliases.get(word, word) for word in _words(value).split())


def _program_ids(facility):
    values = list((facility.get("program_ids") or {}).values())
    values += [row.get("value") for row in facility.get("record_summary", []) if str(row.get("label", "")).endswith("program ID")]
    return {value for item in values for value in re.findall(r"[A-Z0-9]+", str(item or "").upper())}


def resolve_disclosure_links(facilities, identity_data: dict) -> list[dict]:
    """Revalidate reviewed links against each release; do not discover new ones.

    Accepts normalized source facilities or final card facility dictionaries.
    Duplicate IDs, changed identity fields, absent evidence and absent records
    remain candidates. Nothing here changes coordinates, sites or metric scope.
    """
    indexed = {}
    for facility in facilities:
        indexed.setdefault(facility.get("id"), []).append(facility)
    evidence = {item.get("id"): item for item in identity_data.get("evidence", [])}
    output = []
    for original in identity_data.get("links", []):
        link = copy.deepcopy(original)
        expected = link.get("expected_record") or {}
        records = indexed.get(link.get("record_id"), [])
        reasons = []
        if not expected or link.get("relationship") != "within_disclosure_location":
            reasons.append("Unreviewed relationship or missing identity guard")
        if not link.get("evidence_ids") or any(
            not evidence.get(key, {}).get("verified") or
            not re.fullmatch(r"[a-f0-9]{64}", evidence.get(key, {}).get("content_sha256", ""))
            for key in link.get("evidence_ids", [])
        ):
            reasons.append("Reviewed primary evidence unavailable")
        if len(records) != 1:
            reasons.append("Expected record absent or duplicated")
        else:
            record = records[0]
            for key in ("id", "registry_id", "name", "city", "state"):
                if _words(record.get(key)) != _words(expected.get(key)):
                    reasons.append(f"Reviewed {key} changed")
            if _address(record.get("address_line") or record.get("address")) != _address(expected.get("address")):
                reasons.append("Reviewed physical address changed")
            if expected.get("program_id") and expected["program_id"] not in _program_ids(record):
                reasons.append("Reviewed regulator program identifier missing")
        link.update(verification_status="candidate" if reasons else "verified", reason="; ".join(reasons) if reasons else
                    "Reviewed operator/location and government physical-address evidence; exact registry identity retained",
                    allocation_allowed=False, same_physical_scope_verified=False)
        if reasons:
            link["relationship"] = "candidate"
        link["evidence"] = [copy.deepcopy(evidence[key]) for key in link.get("evidence_ids", []) if key in evidence]
        output.append(link)
    return output
