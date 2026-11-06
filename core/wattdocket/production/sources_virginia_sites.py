"""Bounded official DEQ air-site identities, separate from permit legal effect."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlencode

from .fetch import stamp
from .models import SourceResult

SOURCE_ID = "virginia_site_registry"
LAYER_URL = "https://gisdata.deq.virginia.gov/arcgis/rest/services/public/EDMA/MapServer/294"
REGISTRATION_ROOTS = ("11541", "11790", "72374", "73170", "73200", "73363", "73370",
                      "73567", "73643", "73977", "74060", "74094", "81609")
# These regulator sites are outside the main NAICS 518210 search. The EPA
# program identifier, never a name query, supplies the supplemental record.
TARGETED_EPA_ROOTS = ("72374", "73170", "73200", "73977")
QUERY_URL = LAYER_URL + "/query?" + urlencode({
    "where": "PLA_REG_NUM IN (" + ",".join(REGISTRATION_ROOTS) + ")",
    "outFields": "*", "outSR": "4326", "returnGeometry": "true", "f": "json"})
MAX_BYTES = 1_000_000


def parse_virginia_sites(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("DEQ site response exceeds the bounded size")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("error") or payload.get("exceededTransferLimit"):
        raise ValueError("DEQ site query failed or was truncated")
    rows = payload.get("features")
    if not isinstance(rows, list) or not 1 <= len(rows) <= len(REGISTRATION_ROOTS):
        raise ValueError("DEQ site query returned no usable rows or excess records")
    seen, sites, source_ids = set(), [], set()
    for feature in rows:
        a = feature.get("attributes") or {}
        root, site_id = str(a.get("PLA_REG_NUM")), str(a.get("PLA_ID"))
        icis = a.get("PLA_ICIS_ID")
        if (root not in REGISTRATION_ROOTS or root in seen or not site_id.isdigit()
                or not isinstance(icis, str) or not re.fullmatch(r"VA00000051\d{7,9}", icis)
                or icis in source_ids or a.get("FAC_L_STATE") != "VA"
                or not a.get("FAC_L_ADDR_1") or not a.get("FAC_L_CITY")):
            raise ValueError("DEQ site query has ambiguous, missing or unexpected identity fields")
        seen.add(root); source_ids.add(icis)
        # Geometry is retained as publisher evidence, never silently substituted
        # for the independently reviewed inventory point or campus boundary.
        sites.append({"registration_root": root, "regulator_site_id": "va-deq:" + site_id,
            "attributes": a, "geometry": feature.get("geometry"),
            "physical_address": {"street": a["FAC_L_ADDR_1"], "city": a["FAC_L_CITY"],
                                 "state": "VA", "postal_code": a.get("FAC_L_ZIP5")},
            "site_status": {"status_group": a.get("STATUS_GROUP"), "air_operating_status": a.get("AIR_OP_STATUS"),
                "note": "Publisher air-site status; not the legal effectiveness of a permit or evidence of measured operation."}})
    return sorted(sites, key=lambda item: item["registration_root"]), sorted(set(REGISTRATION_ROOTS) - seen)


def collect_virginia_sites(fetcher, *, now: datetime):
    raw = fetcher.get_bytes(QUERY_URL)
    sites, missing = parse_virginia_sites(raw)
    digest = hashlib.sha256(raw).hexdigest()
    receipt = getattr(fetcher, "receipt", lambda _: None)(QUERY_URL)
    if (not receipt or receipt.get("url") != QUERY_URL or receipt.get("sha256") != digest
            or receipt.get("raw_key") != "raw/" + digest + ".gz" or not receipt.get("retrieved_at")):
        raise ValueError("DEQ site identities lack their exact archived source receipt")
    targeted = collect_targeted_epa(fetcher, sites)
    return SourceResult(SOURCE_ID, "Virginia DEQ selected air-site identities", LAYER_URL,
        {"sites": sites, "missing_registration_roots": missing, "queried_registration_roots": list(REGISTRATION_ROOTS),
         "query_url": QUERY_URL, "source_sha256": digest, "source_receipt": receipt,
         "targeted_epa_facilities": targeted,
         "checked_at": stamp(now), "documents_failed": len(missing),
         "note": "Exact regulator air-site identities for the reviewed permit subset. Site activity is not permit legal effectiveness."},
        len(sites), coverage=f"{len(sites)} of {len(REGISTRATION_ROOTS)} requested regulator air sites returned",
        note="Physical addresses and ICIS identifiers come from the official site layer; absent rows remain unresolved. No operating power, water, or emissions are inferred.")


def collect_targeted_epa(fetcher, sites):
    from concurrent.futures import ThreadPoolExecutor
    from .sources_facilities import _url, _results, normalize_echo
    meta_url = _url("air_rest_services.metadata", output="JSON")
    metadata = _results(fetcher.get_json(meta_url))
    columns = metadata.get("ResultColumns") or []
    if not {"RegistryID", "SourceID", "OtherPermitIDs", "AIRStreet", "AIRFIPSCode"} <= {x.get("ObjectName") for x in columns}:
        raise ValueError("Targeted EPA query lost required identity columns")
    qcolumns = ",".join(str(x["ColumnID"]) for x in columns if x.get("ColumnID"))
    selected = [site for site in sites if site["registration_root"] in TARGETED_EPA_ROOTS]
    if len(selected) != len(TARGETED_EPA_ROOTS):
        raise ValueError("Targeted EPA regulator sites are missing from DEQ")

    def one(site):
        query_url = _url("air_rest_services.get_facilities", output="JSON", p_pid=site["attributes"]["PLA_ICIS_ID"], responseset=100)
        query = _results(fetcher.get_json(query_url))
        if str(query.get("QueryRows")) != "1" or not query.get("QueryID"):
            raise ValueError("Targeted EPA program identity is absent or ambiguous")
        row_url = _url("air_rest_services.get_qid", output="JSON", qid=query["QueryID"], pageno=1, qcolumns=qcolumns)
        result = _results(fetcher.get_json(row_url))
        rows = result.get("Facilities")
        if (str(result.get("QueryID")) != str(query["QueryID"]) or str(result.get("QueryRows")) != "1"
                or str(result.get("PageNo")) != "1" or not isinstance(rows, list) or len(rows) != 1):
            raise ValueError("Targeted EPA row count/query changed")
        row = rows[0]
        if (row.get("SourceID") != site["attributes"]["PLA_ICIS_ID"]
                or site["registration_root"] not in re.findall(r"\d+", str(row.get("OtherPermitIDs", "")))
                or not re.fullmatch(r"\d{12}", str(row.get("RegistryID", "")))):
            raise ValueError("Targeted EPA row does not reciprocate the reviewed regulator identity")
        receipts = [getattr(fetcher, "receipt", lambda _: None)(url) for url in (meta_url, query_url, row_url)]
        if any(not receipt or receipt.get("url") != url or not receipt.get("retrieved_at")
               or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256", "")))
               or receipt.get("raw_key") != "raw/" + receipt["sha256"] + ".gz"
               for url, receipt in zip((meta_url, query_url, row_url), receipts)):
            raise ValueError("Targeted EPA row lacks an archived query chain")
        facility = normalize_echo([], rows)[0]
        facility["program_ids"]["air"] = row["SourceID"]
        facility.update(source_lane=SOURCE_ID, supplemental_inventory=True,
                        source_selection="exact_reviewed_deq_icis_id", source_url=row_url,
                        url="https://echo.epa.gov/detailed-facility-report?fid=" + row["RegistryID"],
                        source_receipt=receipts[-1], source_query_receipts=receipts,
                        targeted_registration_root=site["registration_root"])
        return facility

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sorted(pool.map(one, selected), key=lambda item: item["id"])
