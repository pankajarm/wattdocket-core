"""Reviewed government-address links; no building/registration equivalence.

The links pair an exact EPA FRS location row with an independent Massachusetts
UBID building row. They expose related building reports, never an allocation
of the whole building's energy to a tenant, operator or registration.
"""
from datetime import datetime
from urllib.parse import urlencode

from .models import Fetcher, SourceResult
from .facility_disclosure_identity import collect_reviewed_identity
from .sources_facility_reporting import MA_LAYER, ma_location_id

SOURCE_ID = "municipal_disclosure_identity"
REVIEW_VERSION = "municipal-government-address-2026-11-04-v1"

_REVIEWED = (
    {"key": "internap-somerville", "frs": "110024314222", "name": "INTERNAP NETWORK SERVICES",
     "address": "70 INNERBELT RD", "city": "SOMERVILLE", "program": "MAR000545293",
     "ubid": "87JC9WGC+M3Q-24-19-25-20", "ma_address": "70 Inner Belt Rd", "ma_city": "Somerville", "gfa": 273180,
     "extra_note": "The reviewed ECHO air-program row reports Permanently Closed. This is a historical registration-to-building-address relationship, not proof that Internap currently operates or consumes energy there."},
    {"key": "ssb-westborough", "frs": "110070550595", "name": "SSB REALTY WESTBOROUGH DATA CENTER",
     "address": "600 FRIBERG PKWY", "city": "WESTBOROUGH", "program": "MAR000555656",
     "ubid": "87JC7CJ9+9W9-12-20-14-20", "ma_address": "600 Friberg Parkway", "ma_city": "Westborough", "gfa": 99160},
    {"key": "sungard-marlborough", "frs": "110009589267", "name": "SUNGARD AVAILABILITY SERVICES LP",
     "address": "250 LOCKE DR", "city": "MARLBOROUGH", "program": "MAD980522791",
     "ubid": "87JC9C27+XHM-13-17-12-17", "ma_address": "250 Locke Dr", "ma_city": "Marlborough", "gfa": 44204,
     "extra_note": "EPA FRS retains a NAME MISMATCH review flag against EIS 14520211. The link uses its exact FRS identity and location address; it does not assert current operator ownership or resolve that inter-program name review."},
)


def _specs(row):
    frs = {"registry_id": row["frs"], "primary_name": row["name"], "location_address": row["address"],
           "city_name": row["city"], "state_code": "MA"}
    fields = list(frs)
    frs_spec = {"id": "municipal-frs-" + row["key"],
        "url": f"https://data.epa.gov/efservice/FRS_FACILITY_SITE/REGISTRY_ID/{row['frs']}/JSON",
        "publisher": "U.S. Environmental Protection Agency Facility Registry Service", "kind": "json",
        "published_at": None, "row_selector": frs, "display_fields": fields,
        "excerpts": ["; ".join(frs[x] for x in fields)]}
    ma = {"UBID": row["ubid"], "Street_Address": row["ma_address"], "City": row["ma_city"], "Gross_Floor_Area": row["gfa"]}
    fields = list(ma)
    ma_spec = {"id": "municipal-ma-" + row["key"],
        "url": MA_LAYER + "/query?" + urlencode({"where": "UBID='" + row["ubid"] + "'", "outFields": ",".join(fields), "returnGeometry": "false", "f": "json"}),
        "publisher": "Massachusetts Department of Energy Resources", "kind": "arcgis", "published_at": None,
        "row_selector": ma, "display_fields": fields, "excerpts": ["; ".join(str(ma[x]) for x in fields)]}
    return frs_spec, ma_spec


EVIDENCE = tuple(spec for row in _REVIEWED for spec in _specs(row))
REVIEWED_LINKS = tuple({
    "disclosure_location_id": ma_location_id(row["ubid"]), "operator": row["name"],
    "location_label": row["ma_address"] + ", " + row["ma_city"], "reporting_scope": "building",
    "record_id": "echo:" + row["frs"], "relationship": "within_disclosure_location",
    "allocation_allowed": False, "same_physical_scope_verified": False, "coverage_is_exhaustive": False,
    "evidence_ids": ["municipal-frs-" + row["key"], "municipal-ma-" + row["key"]],
    "expected_record": {"id": "echo:" + row["frs"], "registry_id": row["frs"], "name": row["name"],
                        "address": row["address"], "city": row["city"], "state": "MA", "program_id": row["program"]},
    "note": ("The primary EPA FRS location-address row and Massachusetts UBID building report identify the same reviewed street address and city. "
             "The whole-building meter/reporting boundary is not established as equal to this registration or to any operator/tenant. "
             "Show the related building report once without numeric allocation. Registry names are publisher labels, not proof of current ownership. "
             + row.get("extra_note", "")),
} for row in _REVIEWED)


def collect_municipal_identity(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    return collect_reviewed_identity(fetcher, now=now, source_id=SOURCE_ID,
        label="Reviewed municipal building-to-registry address evidence", evidence_specs=EVIDENCE,
        reviewed_links=REVIEWED_LINKS, review_version=REVIEW_VERSION, reviewed_at="2026-11-04", allow_partial=True)
