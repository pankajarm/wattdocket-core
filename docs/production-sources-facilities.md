# Production facility and permit source contract

Research and endpoint probes: November 3, 2026. This document separates
publisher coverage from WattDocket's collection health. A successful fetch does
not make a historical record current, and a NAICS listing does not verify that
a building is a data center.

## EPA ECHO inventory

Primary documentation: [ECHO web services](https://echo.epa.gov/tools/web-services/facility-search-air),
[machine-readable combined-service specification](https://echodata.epa.gov/echo/swaggerx.swagger_json?p_prefix=ECHO),
[air-service specification](https://echodata.epa.gov/echo/swaggerx.swagger_json?p_prefix=AIR),
and [publisher refresh dates](https://echo.epa.gov/resources/echo-data/about-the-data).

The adapter queries both `echo_rest_services` (all media) and `air_rest_services`
under `https://echodata.epa.gov/echo/`. For each service it discovers explicit
column IDs from `metadata?output=JSON`, creates a query using
`get_facilities?output=JSON&p_ncs=518210&responseset=1000`, and retrieves every
page through `get_qid?output=JSON&qid=...&pageno=...&qcolumns=...`.
Explicit columns matter: the combined service's default result omits coordinates
and some cross-program identifiers. Enhanced search can return clusters instead
of records, so it is not the production pagination mechanism.

The November 3 probe found **969 combined-program records** and **479 air-source
records**. These are industry-code search results, not a complete national data
center census. The publisher refresh page listed FRS/ICIS-Air extracts dated
**October 31, 2026**, with the next extract expected November 7. ECHO explains
that source-to-ECHO delays can persist after an agency records an event.

The raw field named `RegistryID` sometimes contains an unlinked program ID,
including `TXR1551ZU` and `VA0000005110774287`. The adapter retains those records
as `echo:<source_record_id>` while setting `registry_id=null` and
`identity_kind=program_record`. Only 12-digit IDs are represented as FRS IDs.
Air sources remain distinct under their `SourceID` and attach by exact published
identity only. Name matches never join records. Different published coordinates
remain visible as conflicts; FRS coordinates take precedence when available.
Missing coordinates remain missing and do not cause a facility to disappear.
Declared coordinate uncertainty is retained in `coordinate_accuracy_m`, using
`FacAccuracyMeters` for the selected FRS point or `CalculatedAccuracyMeters` for
an air-source fallback. It is not inferred from decimal precision. For example,
ECHO reports 17,483 meters for Vantage TX11. Binding must account for this error
when checking region boundaries; precise-looking decimals are not accuracy.

`echo.data` contains `facilities`, `air_records`, `combined_records`,
`publisher_extracted_at`, `publisher_next_extract`, and `refresh_metadata_error`.
Normalized facilities include address, coordinates, raw and normalized status,
program identifiers, original records, classification and identity kind.
Closed facilities retain a closed status; active regulatory registration alone
is not interpreted as physical operation. All code-only classifications start
unverified. A source snapshot fails on truncated/repeated pages, a changed query
count or query ID, duplicate identities, malformed identity, or invalid numeric
coordinates.

One daily bounded query is sufficient to detect the weekly publisher refresh.
For substantially larger coverage, use the official weekly bulk downloads,
not per-facility scraping. The [ECHO downloads page](https://echo.epa.gov/tools/data-downloads)
provides these URLs:

- `https://echo.epa.gov/files/echodownloads/echo_exporter.zip`
- `https://echo.epa.gov/files/echodownloads/frs_downloads.zip`
- `https://echo.epa.gov/files/echodownloads/ICIS-AIR_downloads.zip`
- `https://echo.epa.gov/files/echodownloads/POLL_RPT_COMBINED_EMISSIONS.zip`

The [FRS dictionary](https://echo.epa.gov/tools/data-downloads/frs-download-summary)
defines `FRS_PROGRAM_LINKS.csv` keys `(PGM_SYS_ACRNM, PGM_SYS_ID)` and the FRS
`REGISTRY_ID`. The [ICIS-Air dictionary](https://echo.epa.gov/tools/data-downloads/icis-air-download-summary)
defines source-level `PGM_SYS_ID`, operating-status codes, inspections, tests and
actions. These are the appropriate explicit crosswalks for expansion beyond
NAICS 518210. FRS program linkage is stronger evidence than fuzzy names.

## Virginia DEQ

Publisher: [Issued Air Permits for Data Centers](https://www.deq.virginia.gov/news-info/shortcuts/permits/air/issued-air-permits-for-data-centers).
The direct page returned HTTP 403 in this environment. An explicitly identified
`https://r.jina.ai/<publisher URL>` reading-service response supplied the page
headed **as of Nov. 2, 2026**, containing **194 linked permit rows**.

The legacy parser found only 188 because it discarded six formatting variants:
74063-5, 74288-2, 74302-1, 74333-1, 74348-1 and 74349-1. The production parser
handles HTML tables and Markdown cells, including split/bold links. It preserves
the original `03/26-2026` date and separately normalizes it to ISO. Every linked
permit row must parse; unrecognized/duplicate registrations, conflicting URLs,
or unexpected document hosts reject the snapshot instead of silently reducing
coverage.

`virginia_permits.data` contains the publisher heading date, registry hash,
registry retrieval mechanism/receipt, `permits`, and counts of parsed, failed,
and unfetched documents. Each permit preserves site, registration, raw/ISO issue
date, program, county, office and publisher document URL.

Document collection tries the publisher PDF, then the labeled reading service.
There are at most four simultaneous document requests. The caller must cache
`/home/showpublisheddocument/` URLs, including their reader-prefixed equivalents;
they have no `.pdf` extension. Changed registry URLs naturally request new bytes.
The original receipt date survives a cache hit. Each document records its hash,
representation, retrieval mechanism, receipt, recovered page numbers and errors.
An inaccessible or unreadable document retains its registry entry and a failed
document state with no extracted facts.

Original PDF pages use their physical PDF indices. Reading-service text uses
consecutive printed page headers: the text after header 7 and before header 8
belongs to **printed page 7**. The legacy footer assumption was incorrect.
This order was independently checked against the publisher's
[74190-2 PDF](https://www.deq.virginia.gov/home/showpublisheddocument/25944/638633847472900000)
and [30142-3 PDF](https://www.deq.virginia.gov/home/showpublisheddocument/25646/638754663915730000).
Text preceding the first header, crossing a numbering reset, or following the
last header is withheld because its page boundary is unproven. Ambiguous
duplicate page numbers cannot acquire an invented page citation. Extractor
`va-deterministic-3` emits only deterministic
regex matches whose quote is checked independently against that page. Text
hashes are never labeled as original PDF hashes. PDF extraction requires
PyMuPDF; scanned documents without readable text need a separately validated
OCR path before facts can be published.

Every counted equipment-table row has its own citation. An extracted table
page is not automatically the entire campus fleet. Equipment therefore carries
`scope=equipment_table_page` and `is_complete_fleet=false`; downstream consumers
must not turn it into a campus total or a permit envelope without a separately
validated scope match. Permit ceilings remain distinct from actual operation.

The first live November 3 backfill hit reader HTTP 429 limits. A network-free
replay of captured raw bytes and exact-URL retained repository files recovered
**180 of 194 documents**: 70 recent captures and 110 historical text files. The
historical files have no recorded retrieval timestamp. Their `retrieved_at`
remains null; `archived_by_at`, Git commit, exact bytes and source URL establish
only when they were already archived. Thirteen documents lack verifiable
bounded text; changed revision 74118-4 has no exact retained document. Their
registry entries remain visible with no extracted facts.

`scripts/reparse_virginia_archive.py` defaults to a dry run; `--write` preserves
the old complete state and errors, then uses conditional state replacement.
It never inserts a fabricated HTTP receipt. The collector reuses an exact
registration+versioned URL for at most 30 days based on a recorded retrieval or
archival date while retaining its original date semantics. A changed URL is
fetched anew. Failed refreshes may retain exact-document v3 last-known facts,
explicitly marked stale with the latest error. Counters separate current parsed,
cached, stale and unavailable documents. Reader throttling belongs to the shared
fetcher; a rate limit must defer requests rather than create a retry burst.

## Optional OpenStreetMap discovery

The [OSM data-center tag documentation](https://wiki.openstreetmap.org/wiki/Tag:telecom%3Ddata_center)
uses `telecom=data_center`, `building=data_center`, and British-spelled
`industrial=data_centre`, plus construction/proposed lifecycle prefixes.
The adapter also tolerates the alternate `industrial=data_center` spelling.
It queries US administrative area elements through Overpass, preserving element
type/ID, version, edit timestamp, tags and representative coordinates. Query
timeouts, an Overpass `remark`, missing database timestamp, duplicate element
IDs or unexpected empty results are failures, never a smaller successful list.
The first endpoint returned 406 and a second endpoint timed out during initial
probes; this is an optional source and its health must remain visible.

Coordinate derivation is explicit. Nodes supply community-mapped points with
unknown positional accuracy. Overpass `out center` supplies the **bounding-box
center** for ways and relations; the point can lie outside a concave mapped
area, as the [Overpass output documentation](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL#Print_(out))
explains. Without the mapped extent or separate location verification, these
area centers are withheld from both map pins and regional-grid assignments.
They remain searchable source records, not verified physical sites.

Fresh imports retain `coordinate_source` and collection method. Replays of
older captured records derive this provenance from the retained element type
or immutable `osm:way:`/`osm:relation:` identifier, explicitly marking that
inference. They do not change source bytes, hashes or capture dates. Independent
publication and release-verifier checks infer the area type from the public ID
and reject a map pin, grid rate, alternative factor or scenario even if someone
incorrectly flips its location-quality flag.

A November 3 capture retained 2,067 elements: 141 nodes, 1,906 ways and 20
relations. The failed subsequent refresh remains stale with its original
22:03:42 UTC successful-capture timestamp. Replaying that corpus under the
corrected policy withholds all 1,926 area centers while retaining the 141 node
points as approximate community locations. This is evidence for that captured
corpus, not a promise that future inventory counts stay fixed. See
`docs/verification/osm-center-retention-review-2026-11-03.json`.

OSM objects are explicitly **community-mapped**, never regulator-verified.
Buildings, nodes and campuses can overlap and are not silently collapsed. No
proximity-only or operator-name joins are performed. A later identity review
may link an exact external identifier or a distinctive exact facility name plus
location; ambiguous candidates require review.

OSM data is [ODbL licensed](https://www.openstreetmap.org/copyright). Preserve
`© OpenStreetMap contributors`, the license link, source IDs and ODbL terms in
the UI and data exports. Keep this source distinguishable in stored/derived
datasets. The public Overpass service is not a guaranteed production SLA;
bounded scheduled refresh and last-good preservation are necessary.

## Explicit exclusions and additional opportunities

**PeeringDB:** its guest API is technically reachable (November 3 probe: 1,379 US
facility records, 1,356 with coordinates), and its API supports `limit`, `skip`,
`since` and deleted records. However, the [current acceptable-use policy](https://www.peeringdb.com/aup)
restricts storage, reproduction, redistribution and commercial uses outside
approved purposes. It is excluded from production until appropriate permission
exists. No request for permission has been sent.

**Texas permits:** TCEQ now directs document searches to its
[Central File Room](https://www.tceq.texas.gov/permitting/air/air_status_permits.html),
at `https://records.tceq.texas.gov/cs/idcplg?IdcService=TCEQ_SEARCH`.
The old air document server is retired; the status search returned 401 during
the probe. A public document can be fetched by its stable observed `dID` through
`TCEQ_EXTERNAL_SEARCH_GET_FILE`, but fetching a known document does not discover
new permits or establish that it remains effective. Historical draft documents
must retain their draft state. A complete Texas collection needs a validated
registry/discovery adapter and document-version selection, not just a refreshed
copy of four retained demo PDFs.

`collect_texas` implements explicitly selected-document monitoring, with
`source_id=texas_permits` and `data.documents`. Its November 3 live check fetched
and parsed all four selected primary-source PDFs: Amazon ALE60 technical review
(`dID=8724316`, 2 pages), Compass O4679 draft permit (`dID=8935965`, 93 pages),
Vantage TX2 draft statement of basis (`38600sob.pdf`, 14 pages), and Vantage TX11
draft statement of basis (`dID=9551548`, 14 pages). The selected-documents manifest
in the adapter contains exact observed publisher URLs. Every record keeps
`document_type`, SHA-256, original retrieval receipt and
`is_current_effective_limit=false`; `latest_effective_status_verified=false`
applies to the collection. Facts are verified document statements, not proof of
current effective limits. A publisher-prepared date is retained where present.
The caller should cache `IdcService=TCEQ_EXTERNAL_SEARCH_GET_FILE` URLs as
documents even though their paths lack a PDF extension.

`texas_evidence.for_facility` links only an exact published RN or exact street
address plus city and Texas state. Conflicting known RNs prevent an address
fallback. Live replay linked TX2 by RN111926465 and TX11 by 14720 Omicron Drive,
San Antonio. It exposes draft metadata and verified verbatim statements, never
legacy inferred fleet counts or generic regulatory engine-rating thresholds.
It does not invent matches for the two selected documents without a confident
facility identity in the current ECHO inventory.

**GHGRP/FRS:** the legacy
`https://data.epa.gov/efservice/FRS_FACILITY_SITE/REGISTRY_ID/110072155814/JSON`
returned the Vantage address, while the analogous
`PUB_DIM_FACILITY/FRS_ID/110072155814/JSON` query returned an empty list. That is
absence of a matched report, never zero emissions. The current
[Envirofacts API documentation](https://www.epa.gov/enviro/envirofacts-data-service-api)
promotes `https://data.epa.gov/dmapservice/[schema.table]/.../1:1000/sort/.../JSON`,
with explicit sorting and pagination. The publisher's
[GHGRP data page](https://www.epa.gov/ghgreporting/find-and-use-ghgrp-data)
still identifies 2023 as its latest public reporting year. Probe actual tables
and preserve reporting year before claiming a more recent inventory. GHGRP is
direct emissions reporting, not a source of site electricity use or water use.

## Publication gates

Source retrieval and source observation dates are separate. Persist raw hashes,
parser version and exact request URLs. Retain the last good snapshot when a
source fails; report stale/failed source health without advancing its observation
or original retrieval timestamp. Registry completeness is required for that
source to succeed, while document-level failures remain explicitly enumerated.
No dataset currently described here warrants a claim of every US data center,
measured facility load, measured water use, or real generator run hours.
Adapter and local replay validation do not establish production delivery;
deployment, scheduled execution and browser verification remain separate gates.
