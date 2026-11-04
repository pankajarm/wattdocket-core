# Operator-reported campus observations

`collect_meta_actuals` in `core/wattdocket/production/sources_operator_actuals.py`
accepts Meta's reviewed 2025 Environmental Data Index and emits 90 annual FY2024
observations for 18 named worldwide campuses (15 US). These are disclosed campus
quantities. A campus may contain multiple buildings; the adapter does not identify
individual EPA/OSM facilities or allocate campus quantities to them.

The selected index is the latest edition linked from [Meta's sustainability
homepage](https://sustainability.atmeta.com/) in the captured response of November
3, 2026. The [primary PDF](https://sustainability.atmeta.com/asset/2025-environmental-data-index/)
contains 19 pages, reports fiscal year 2024, and has SHA-256
`d7527cd23308869105b404b486117603bb3f5ee10e30499367f8698110afb66d`.
The report year is 2025. Its exact publication date is not established; PDF
creation/modification and HTTP Last-Modified timestamps remain separate evidence.
Retrieval in November 2026 does not change the observation year.

| Published table | PDF page | Canonical metric | Preserved unit |
| --- | --- | --- | --- |
| Electricity consumption by facility | 6 | `electricity_consumption` | MWh |
| Water withdrawal by facility | 9 | `water_withdrawal` | million liters (equivalent to source megaliters) |
| Market-based Scope 1 and 2 emissions | 3 | `emissions_scope1_and2_market` | metric tonnes CO2e |
| Scope 2, market-based column | 4 | `emissions_scope2_market` | metric tonnes CO2e |
| Scope 2, location-based column | 4 | `emissions_scope2_location` | metric tonnes CO2e |

For example, Altoona's FY2024 rows report 1,585,392 MWh electricity, 242 million
liters water withdrawal, 1,396 metric tonnes combined Scope 1 and market-based
Scope 2, zero market-based Scope 2, and 666,434 metric tonnes location-based Scope
2. The zero is the publisher's market-based accounting result. It does not imply
zero electricity use or zero physical grid emissions.

The report's methods on pages 13–16 and 18 allow activity-data estimates where
necessary. The adapter labels every row `operator_reported`,
`is_meter_observation: false`, and `metered_share_known: false`. It preserves the
source's operational-control scope. Electricity is annual energy, not demand in
MW or an hourly series. Water withdrawal is not consumption; the selected water
table excludes separately reported construction withdrawal. Combined Scope 1
and Scope 2 is not Scope 1 alone, and the adapter does not derive a separate
Scope 1 value by subtraction. The reviewed report does not identify a specific
GWP assessment/version for these campus emissions rows; `gwp_basis` remains null.
The publisher's reported CO2e totals are not recalculated using eGRID.

## Contract and identity boundary

Source ID is `meta_actuals2024`. `SourceResult.record_count` counts 90 campus
observations. `data.locations` contains 18 stable `meta:<location>` IDs, original
publisher labels, operator, and `reporting_scope: campus`. `data.observations`
contains five metrics per location, with stable observation IDs, values, units,
FY2024 inclusive start/exclusive end (`2024-01-01T00:00:00Z` to
`2025-01-01T00:00:00Z`), reporting and methodology notes, original PDF page/table/
row/column locators, publisher, source URL, parser version, and PDF SHA-256.

Every adapter row has `facility_binding_verified: false`. A separate reviewed
identity layer may associate an inventory facility with the disclosed campus.
Such a membership link does not establish that their physical scopes are equal,
or authorize copying a campus total onto each inventory record. The report
contains no campus postal-address or EPA-ID crosswalk.

`data.aggregates` preserves 25 total/leased/other/offices observations with
`reporting_scope: operator_aggregate` and no disclosure-location ID. They remain
separate from location counts and must never be represented as individual sites.
`data.validation` contains count, uniqueness and reconciliation results;
`data.source` records edition selection, hash, report/observation years and method.

## Release gates and refresh policy

The collector uses the common cached fetcher; two network resources and a 2.27 MB
PDF need no paid API or new runtime dependency. PyMuPDF is already pinned in the
production requirements. A weekly refresh is sufficient for this annual report.
The official homepage must still link the reviewed edition as its newest index.
A newer linked index, missing official link, revised PDF hash, malformed PDF,
changed table units/year/column order, missing or additional location, qualified
latest-year value, or unreviewed methodology fails closed. The common pipeline
can retain previously validated data with its original period and stale status;
it must not advance its checked/observation dates after a failure.

Parsing checks all reviewed rows and five year columns. Historical missing or
qualified cells can be skipped structurally; a missing/qualified latest value
cannot become zero. Scope 2 columns are explicitly paired by market/location.
The 18 campus rows plus leased/other quantities reconcile to the published
data-center aggregate within 10.5 whole units, reflecting 20 component cells and
one total rounded to the nearest whole unit. Data-center plus offices reconcile
to the overall total within 1.5. Original rounded values are never rewritten to
force agreement. Combined Scope 1+2 market-based emissions cannot be smaller
than the Scope 2 market-based component beyond rounding tolerance.

The 22 dedicated regression tests cover genuine PDF extraction, period and unit
preservation, explicit zero versus missing data, scope isolation, paired columns,
unparsed rows, unreviewed revisions/methods, and internally inconsistent totals.
Real-source capture hashes, reconciliation results, and representative values
are in `docs/verification/meta-operator-actuals-2026-11-03.json`. The underlying
report remains copyrighted by Meta; the product attributes factual values and
links the report rather than publishing a copy of its prose or pages.

## Google electricity coverage gap

The separately integrated [Google 2026 Environmental Report](https://sustainability.google/reports/google-2026-environmental-report/)
provides FY2025 water withdrawal, discharge and consumption for 40 named
worldwide locations. Its electricity table on PDF page 93 reports geographic
regions and fleet totals: 42,415,800 MWh for data centers and 43,586,600 MWh
globally. This review found no named-campus annual electricity table. Campus
CFE percentages, PUE ratios and renewable procurement capacity do not establish
campus consumption. No site electricity observation is manufactured from those
values or from fleet totals. Google water remains a separately scoped disclosure
until an independent identity review establishes a specific campus relationship.
