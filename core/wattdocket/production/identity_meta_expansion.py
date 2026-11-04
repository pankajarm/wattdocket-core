"""Reviewed Meta campus coverage from primary ownership and street records.

These are explicit reviewed rules, not an alias/name matcher. A whole-campus
disclosure stays on its reporting location; it is never allocated to an EPA
registration, OSM polygon, building, meter, or point on the map.
"""
from __future__ import annotations

from datetime import datetime

from .facility_disclosure_identity import collect_reviewed_identity
from .models import Fetcher, SourceResult

SOURCE_ID = "meta_disclosure_identity"
REVIEW_VERSION = "meta-physical-coverage-2026-11-04-v1"

EVIDENCE = (
    {"id": "meta-expand-subsidiaries", "kind": "html", "publisher": "Meta Platforms, Inc. (SEC filing)",
     "url": "https://www.sec.gov/Archives/edgar/data/1326801/000132680125000017/meta-12312024x10kexhibit211.htm",
     "published_at": None, "document_period_end": "2024-12-31",
     "excerpts": ["LIST OF SUBSIDIARIES META PLATFORMS, INC.", "Siculus, Inc.", "Sidecat LLC",
                  "Stadion LLC", "Greater Kudu LLC", "Morning Hornet LLC", "Raven Northbrook LLC",
                  "Velvet Tech Services, LLC"]},
    {"id": "meta-expand-locations", "kind": "html", "publisher": "Meta", "published_at": None,
     "url": "https://datacenters.atmeta.com/us-locations/",
     "excerpts": ["iowa Altoona", "missouri Kansas City", "nebraska Sarpy", "new mexico Los Lunas",
                  "ohio New Albany", "texas Fort Worth", "Utah Eagle Mountain", "georgia Stanton Springs"]},
    {"id": "meta-expand-altoona", "kind": "html", "publisher": "Iowa Department of Natural Resources",
     "url": "https://www.iowadnr.gov/public-meeting-or-comments/2024-02-29/air-quality-permits-under-review-published-2292024",
     "published_at": None, "document_date": "2024-02-29",
     "excerpts": ["Siculus, Inc., 100 Share Way, Altoona, Iowa 50009."]},
    {"id": "meta-expand-fort-worth", "kind": "pdf", "publisher": "City of Fort Worth Water Department",
     "url": "https://www.fortworthtexas.gov/files/assets/public/v/16/water/documents/pretreatment/business-id-list-address.pdf",
     "published_at": None, "document_date": "2025-05-02", "page": 73,
     "expected_sha256": "1d64b8234d637e2dc7cb5a4915a302a1db34826486847784b2ba677e2a4c7af6",
     "excerpts": ["013390 FACEBOOK DATA CENTER FTW 4500 LIKE WAY FORT WORTH TX 76177"]},
    {"id": "meta-expand-kansas-city", "kind": "pdf", "publisher": "Missouri Department of Natural Resources",
     "url": "https://dnr.mo.gov/sites/dnr/files/vfc/2025/09/main/velvet-tech-services-kansas-city-op092025-004.pdf",
     "published_at": None, "document_date": "2025-09-04", "page": 3,
     "expected_sha256": "73566bd9c467caf427e24c6dc4a4ae907d676d573266b74d1b324843b999ce91",
     "excerpts": ["Installation ID: 047-0211", "Installation Name and Address Velvet Tech Services, LLC 1800 Northwest 128th Street Kansas City, MO 64164"]},
    {"id": "meta-expand-sarpy", "kind": "pdf", "publisher": "Nebraska Department of Water, Energy, and Environment",
     "url": "https://dwee.nebraska.gov/sites/default/files/publications/2025%20Emission%20Inventory%20Request%20List.pdf",
     "published_at": None, "page": 13,
     "expected_sha256": "7a27249abc21ec2e2bca8bc5337201484cb8f5461ff7798863540cf17ff336b4",
     "excerpts": ["108432 Raven Northbrook LLC 14734 Friend Plz Springfield"]},
    {"id": "meta-expand-stanton-springs", "kind": "pdf", "publisher": "Georgia Environmental Protection Division",
     "url": "https://epd.georgia.gov/document/document/pa1123-2/download",
     "published_at": None, "page": 3,
     "expected_sha256": "ed94548c940a90faa5893cec036f28f867c030997688251115f1c1e9d0f56968",
     "excerpts": ["Facility Name: Morning Hornet, LLC Application No: 28974 Facility Address: 100 Move Fast Way, Social Circle, 30025"]},
    {"id": "meta-expand-new-albany", "kind": "pdf", "publisher": "Ohio Department of Development",
     "url": "https://dam.assets.ohio.gov/image/upload/v1766970661/development.ohio.gov/community/redevelopment/COMBINED_-_Mega_Project_Map_-_12.2025.pdf",
     "published_at": None, "document_date": "2025-12-19", "page": 2,
     "expected_sha256": "0905d218d1f6195a56549f6d9fdabd438ecd1b8a2406a2ca0f368b152a25ef82",
     "excerpts": ["Sidecat LLC Information Services and Software 2017 1500 Beech Rd SW New Albany 43054"]},
    {"id": "meta-expand-los-lunas", "kind": "pdf", "publisher": "New Mexico Environment Department",
     "url": "https://www.env.nm.gov/public-notices/wp-content/uploads/sites/32/2024/04/Original-Application-7026M6.pdf",
     "published_at": None, "document_date": "2024-03-11", "page": 24,
     "expected_sha256": "1801ad6bdfe883497119bbffeff8ebbcf7f6d6d8a99bf181cf96b9614bb20aca",
     "excerpts": ["Facility Name: Greater Kudu LLC", "Facility Street Address (If no facility street address, provide directions from a prominent landmark): 4250 Messenger Loop NW, Los Lunas, NM 87031"]},
    {"id": "meta-expand-eagle-mountain", "kind": "html", "publisher": "Utah County Land Records",
     "url": "https://www.utahcounty.gov/landrecords/property.asp?av_serial=666570001001",
     "published_at": None,
     "excerpts": ["Serial Number: 66:657:0001", "Addrs 2019... STADION LLC", "1275 N COMMUNITY CIR - EAGLE MOUNTAIN"]},
)


def _link(location, label, record_id, name, address, city, state, document, *, program=None, note):
    return {
        "disclosure_location_id": f"meta:{location}", "operator": "Meta", "location_label": label,
        "reporting_scope": "campus", "record_id": record_id,
        "relationship": "within_disclosure_location", "allocation_allowed": False,
        "same_physical_scope_verified": False, "coverage_is_exhaustive": False,
        "evidence_ids": ["meta-expand-locations", "meta-expand-subsidiaries", document],
        "expected_record": {"id": record_id, "registry_id": record_id.split(":", 1)[1] if record_id.startswith("echo:") else None,
                            "name": name, "address": address, "city": city, "state": state, "program_id": program},
        "note": note + " This is reviewed campus coverage only. Keep the reported total once at the disclosure location; "
                       "no allocation to this record, no identical-boundary claim, and no change to map/grid eligibility.",
    }


REVIEWED_LINKS = (
    _link("altoona-ia", "Altoona (IA)", "echo:110070253862", "SICULUS, INC", "100 SHARE WAY NW", "ALTOONA", "IA",
          "meta-expand-altoona", note="Iowa DNR identifies Siculus's physical site at 100 Share Way, Altoona. "
          "The EPA address includes the NW directional suffix; Meta's filing establishes Siculus ownership."),
    _link("fort-worth-tx", "Fort Worth (TX)", "echo:110071163236", "ALLIANCE SITE", "4500 LIKE WAY", "FORT WORTH", "TX",
          "meta-expand-fort-worth", program="TX0000004843901764", note="The city water register explicitly identifies Facebook Data Center FTW at the "
          "same complete street address as EPA's Alliance Site. It lists multiple buildings; this is not building-level consumption."),
    _link("kansas-city-mo", "Kansas City (MO)", "echo:110071909956", "VELVET TECH SERVICES", "1800 NW 128TH ST", "KANSAS CITY", "MO",
          "meta-expand-kansas-city", program="MO0000002904700211", note="Missouri's installation address and installation ID 047-0211 establish "
          "the Velvet Tech record; Meta's SEC subsidiary list establishes the parent operator."),
    _link("sarpy-ne", "Sarpy (NE)", "echo:110071150094", "RAVEN NORTHBROOK LLC", "14734 FRIEND PLZ", "SPRINGFIELD", "NE",
          "meta-expand-sarpy", program="NE0000003115300182", note="Nebraska's facility inventory identifies Raven Northbrook at 14734 Friend Plz, "
          "Springfield. This physical address is distinct from the operator's corporate mailing address."),
    _link("stanton-springs-ga", "Stanton Springs (GA)", "echo:110070156253", "MORNING HORNET, LLC", "100 MOVE FAST WAY", "SOCIAL CIRCLE", "GA",
          "meta-expand-stanton-springs", note="Georgia's notice explicitly labels this Morning Hornet address as the facility address. "
          "Meta's SEC filing establishes the subsidiary; the address is within the named Stanton Springs operator location."),
    _link("new-albany-oh", "New Albany (OH)", "echo:110070887474", "SIDECAT LLC (0145000554)", "1300 -1700 BEECH RD SW", "NEW ALBANY", "OH",
          "meta-expand-new-albany", note="Ohio identifies Sidecat's site at 1500 Beech Rd SW, within EPA's reviewed 1300–1700 Beech Rd SW "
          "address range. The range does not establish equal building, campus, meter, or regulatory boundaries."),
    _link("los-lunas-nm", "Los Lunas (NM)", "osm:way:578412815", "Meta Los Lunas Data Center", "4250 Messenger Loop Northwest", "Los Lunas", "NM",
          "meta-expand-los-lunas", note="NMED's application explicitly provides Greater Kudu's facility street address, not just a contact address. "
          "It matches the reviewed OSM campus street address (NW/Northwest). The incomplete-address EPA record is not joined."),
    _link("eagle-mountain-ut", "Eagle Mountain (UT)", "osm:way:1035123482", "Meta Eagle Mountain Data Center", "1275 North Community Circle", "Eagle Mountain", "UT",
          "meta-expand-eagle-mountain", note="Utah County's parcel 66:657:0001 identifies owner Stadion LLC and lists 1275 N Community Cir "
          "among its physical addresses. Meta's SEC filing establishes Stadion ownership. The parcel covers multiple addresses; "
          "the OSM polygon extent is not independently verified."),
)


def collect_meta_identity(fetcher: Fetcher, *, now: datetime) -> SourceResult:
    return collect_reviewed_identity(fetcher, now=now, source_id=SOURCE_ID,
        label="Reviewed Meta campus identity evidence", evidence_specs=EVIDENCE,
        reviewed_links=REVIEWED_LINKS, review_version=REVIEW_VERSION,
        reviewed_at="2026-11-04", allow_partial=True)
