# Government facility reporting sources

Validated against public publisher responses on 2026-11-03. These adapters
collect annual reporting entities and quantities. They do not assert that an
EPA inventory record is a complete physical datacenter or allocate a campus
total to its buildings. Independent reviewed links belong to the disclosure
identity source.

## Massachusetts building energy

`collect_massachusetts_reporting` / source ID `massachusetts_energy` reads the
[state's LBER results application](https://www.mass.gov/info-details/large-building-energy-reporting-results),
whose [public application configuration](https://www.arcgis.com/sharing/rest/content/items/47c49604b161458aac09dc472b0576ae/data?f=pjson)
links the [2026 annual report feature service](https://services1.arcgis.com/7iJyYTjCtKsZS1LR/arcgis/rest/services/2026_10_28_LBER_Annual_Report_Data/FeatureServer/0).
The reviewed layer's `F2025_Total_GHG_Emissions_Metric_Tons_COe` column explicitly
identifies the observation year as **2025**. The 2026 dataset name and retrieval
date do not become the observation year. The state page warns about incomplete
Boston BERDO submissions and invites corrections to missing energy use.

The collector selects nine reviewed UBIDs with one bounded query; it does not
claim statewide datacenter coverage. All nine must return exactly once. Seven
currently have annual grid-electricity and calculated GHG quantities, for
**14 observations**. MGHPCC and 600 Winter Street have no 2025 quantities and
remain visible reporting entities with explicit missing metrics. No observation
is manufactured for a null cell. This dataset supplies no water-use column.

| UBID | Publisher address/city | 2025 grid electricity, kBtu |
| --- | --- | ---: |
| 87JC9WGC+M3Q-24-19-25-20 | 70 Inner Belt Rd, Somerville | 106,898,400 |
| 87JC9PVM+6RG-14-14-14-14 | 115 2Nd Ave, Waltham | 72,998,250 |
| 87JC7CJ9+9W9-12-20-14-20 | 600 Friberg Parkway, Westborough | 47,505,930 |
| 87JC7826+GX5-21-26-18-28 | 8 Centennial Dr, Shrewsbury | 47,345,000 |
| 87JC8CG5+H5J-18-18-15-17 | 555 Forest Street, Marlborough | 46,226,660 |
| 87JCHQ2M+7VV-32-27-31-23 | 41 Alexander Rd, Billerica | 23,932,780 |
| 87JC9C27+XHM-13-17-12-17 | 250 Locke Dr, Marlborough | 11,558,490 |
| 87J9692R+XVR-18-18-17-18 | 100 Bigelow St, Holyoke | missing |
| 87JC9PWJ+QM4-20-12-19-10 | 600 Winter St, Waltham | missing |

The `electricity_consumption` metric retains **kBtu**, an energy unit. It is
specifically electricity purchased from the grid, not all onsite generation or
instantaneous demand. `reported_ghg_total` retains the state's metric tons CO2e
and is labeled `publisher_calculated_from_reported_energy`. The state describes
these emissions as estimated from energy. No direct emissions sensor or
separately established Scope 1 or Scope 2 accounting boundary is claimed.
For example, the state publishes 113,182 metric tons CO2e for 70 Inner Belt;
its raw row also reports 2,008,266,000 kBtu natural gas. We preserve the source
figures, do not recalculate them, and do not certify their underlying reporting
accuracy.

Entity IDs are `ma-lber:` plus lower-case UBID with `+` replaced by `-`.
The exact UBID remains in `source_identifiers.ma_ubid`. The complete selected
source row, building type, postal address and missing-metric list are retained.
The publisher does not identify an operator, so `operator` is null. The source
row for 8 Centennial explicitly says **Shrewsbury**, while an inventory candidate
says Grafton; matching must not erase that conflict. A single 115 Second Avenue
building must not inherit the entire 115-and-125 EPA record boundary.

The collector checks the live application still points at the reviewed annual
layer and no newer recognized annual edition has appeared. Changed units/year,
missing/duplicate UBIDs, truncated ArcGIS responses, numeric corruption, and a
new parent/child boundary fail the source. Caller last-good retention applies;
a changed publication needs schema and scope review before becoming current.

## EPA GHGRP direct combustion

`collect_ghgrp_reporting` / source ID `ghgrp` queries the primary
[GHGRP facility dimension for NAICS 518210](https://data.epa.gov/efservice/PUB_DIM_FACILITY/NAICS_CODE/518210/ROWS/0:999/JSON)
and bounded per-facility `PUB_FACTS_SUBP_GHG_EMISSION` rows. It validates the live
gas and subpart dictionaries and only accepts direct emitters reporting
**Subpart C stationary fuel combustion**. Power plants and semiconductor records
with other NAICS codes are not included to inflate datacenter coverage.

The current response contains six distinct regulatory facilities and ten
facility-year records, with public data through **2023**, not 2026. EPA's
[current data-sets page](https://www.epa.gov/ghgreporting/data-sets) still links
the 2023 summary release. The
[EPA methodology explanation](https://www.epa.gov/ghgreporting/ghgrp-methodology-and-verification)
distinguishes reporting/calculation methods; these observations are reported
emissions, not continuous stack or electricity measurements.

| GHGRP entity | Source name | FRS identifier | Latest 2023 total, metric tons CO2e |
| --- | --- | --- | ---: |
| 1014330 | Crusoe – Eagle 4-9 | 110071232148 | 3,439.888 |
| 1014530 | Crusoe – Oxbow | 110071232148 | 25,760.004 |
| 1014567 | Crusoe – Surprise S4 | 110071232148 | 25,077.456 |
| 1014838 | Crusoe – Snapper | 110071232148 | 64,435.008 |
| 1014631 | Crusoe – Crosby Chase CTB | 110071397412 | 41,464.144 |
| 1014632 | Crusoe – Kraken CDP | 110071397413 | 22,914.564 |

These totals sum EPA's already-converted CO2e rows for CO2, CH4 and N2O;
biogenic CO2 is excluded. We do not apply GWPs again. All expected components
must exist exactly once and match their facility, annual dimension and Subpart C.
The observation retains each component row and the archived response hash. The
unit and exclusion follow EPA FLIGHT's published total-facility convention
([example primary page](https://ghgdata.epa.gov/flight/details/1006402/2023/E)).

IDs are `ghgrp:<facility_id>`. Exact GHGRP, FRS and e-GGRT identifiers are retained.
The four facilities sharing FRS 110071232148 also share a Denver corporate
contact address; they stay four reporting entities and are never combined or
represented as emissions from that Denver address. The ND/MT entities have
reciprocal FRS IDs, but any inventory link still requires the identity review.
The GHGRP threshold/coverage excludes many sites and does not supply purchased
electricity Scope 2, water, or a whole-operator footprint.

## Other investigated sources and limits

Boston's official BERDO page links its 2026 disclosure, whose public CKAN
resource metadata is available at
[resource c6edf41e-eb53-45a4-9c52-837b865bf19f](https://data.boston.gov/api/3/action/resource_show?id=c6edf41e-eb53-45a4-9c52-837b865bf19f).
The public XLSX download returned HTTP 403 in this environment on 2026-11-03.
Its contents were not inspected, and no Boston electricity or water observations
are published by these adapters. This is a recorded upstream availability gap,
not a successful water pipeline. Seattle/NYC expansion was deferred in favor of
the verified Massachusetts and EPA sources.

## Validation and archival contract

Live replay uses the production `CaptureFetcher`, which captures exact bytes,
SHA-256, response metadata and retrieval time under its private archive. Parsed
results and receipts were written under `.production/facility-reporting-capture/`.
Retrieval dates never replace reporting periods. Every adapter observation uses
the shared reporting-entity contract, `facility_binding_verified:false`, a
native unit, inclusive/exclusive annual UTC bounds, source URL/field/quote and
source hash. No new service, key, subscription or metering API is needed.

Focused tests use captured primary rows and deliberately corrupt identity,
year, gas completeness, subpart, quantities, units, UBID completeness and
reporting boundaries. Fixtures preserve capture receipts for reproducibility.
Source completion does not by itself establish production release readiness;
the parent pipeline, identity review, browser presentation and live acceptance
must all pass before the delivery is complete.
