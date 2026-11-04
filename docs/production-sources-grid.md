# Production grid source contracts

Implemented in `core/wattdocket/production/sources_grid.py`. All adapters accept
an injected `Fetcher` and an aware `now` timestamp, return `SourceResult`, and
perform no external writes. The production runner owns retries, raw-response
archival, freshness policy and publication of the last validated release.

## EPA eGRID annual rates

- Discover the latest official workbook linked from
  <https://www.epa.gov/egrid/detailed-data>. Only HTTPS EPA-hosted workbook links
  are accepted. Select the newest data year and revision, then verify that the
  workbook's rows agree with that year and contain all 27 subregions.
- Read the `SRLyy` sheet with the existing deterministic parser. CO2e rate is
  `SRCO2EQA (short tons/year) × 2000 / SRNGENAN (MWh/year)`, in lb CO2e/MWh.
- Retain workbook SHA256, URL, edition, sheet and source data year on the result.
  The annual observation period and the time the release page was checked are
  separate. A new fetch date never makes an old annual dataset current-year data.
- Require explicit workbook units for generation, individual gas masses and gas
  output rates. Mechanically identify the CO2e GWP basis against all 27 annual
  component rows; unsupported or ambiguous methods refuse publication. Current
  EPA 2023 rows reconcile to IPCC AR5 100-year weights (CO2=1, CH4=28, N2O=265).
  Each row includes publisher, license, units, accounting basis, GWP weights,
  annual period, publication date and limitations.
- Return `data = {rates: {CODE: rate_record}, source: {...}}`.
- Annual location-based accounting is not an hourly carbon signal, a marginal
  emissions estimate, a measurement of a facility, or a serving-utility claim.

On November 3, 2026, EPA's official page still linked **eGRID 2023 rev2**. The
collector retrieved and parsed 27 rates successfully. A newer third-party
reconstruction is not substituted for an EPA publication.

The official page explicitly reports revision 2's publication date as
**June 12, 2025**. The adapter extracts that date from the selected edition and
revision's page section; absent matching evidence remains null. This is distinct
from the workbook's 2023 data period and its November 2026 retrieval. EPA's
[FAQ section 11](https://www.epa.gov/egrid/frequent-questions-about-egrid)
recommends the latest available edition for ongoing work and the preceding
edition when the corresponding year's data is unavailable. A clearly disclosed
historical factor can therefore support a proxy estimate when actual electricity
consumption and an appropriate regional assignment exist. Factor age alone does
not make such a proxy invalid. It does not turn a hypothetical load into an
observed facility footprint.

## Independent 2024 regional comparison

`core/wattdocket/production/sources_grid_alternative.py` provides
`collect_cornerstone_egrid2024(fetcher, now=...)` with source ID
`cornerstone_egrid2024` and the same `{rates, source, validation}` result shape.
It is an optional, separately labeled comparison, never an automatic replacement
for the official EPA baseline. It provides annual regional-code context;
**no matching 2024 boundary layer or facility/utility assignment is verified**.
EPA 2023 geographic assignments remain tied to EPA 2023 factors.

The pinned public source is Cornerstone Sustainability Data Initiative's
[Zenodo record 18968658](https://zenodo.org/records/18968658), release 1.0.0,
published March 17, 2026, DOI `10.5281/zenodo.18968658`, data license CC-BY-4.0.
The record's `metadata.txt` identifies the EPA development-code commit
`8932a073aa4c0dac227f953e2805d5f64634b57e`; that code has an MIT license. Preserve
the code commit and URL, raw file SHA256, publisher MD5/size, version and source
period. Source input timestamps written with `-00:00` retain that literal unknown
offset instead of being relabeled confirmed UTC. No API key or payment is needed.

The actual release has **three conflicting method representations**:

| Representation | Mechanically verified 100-year weights | Treatment |
| --- | --- | --- |
| Zenodo description | States AR5: CH4=28, N2O=265 | Retain as publisher's declaration |
| Metric workbook annual CO2e mass | AR6 fossil methane: CH4=29.8, N2O=273 | Preserve original mass and rate separately |
| Companion comparison workbook CO2e rate | AR6 non-fossil methane: CH4=27, N2O=273 | Preserve original comparison rate separately |
| WattDocket comparison | Explicit AR5: CH4=28, N2O=265 | Recalculate from unrounded annual component masses |

All 27 rows must independently reconcile to the reviewed published methods.
The AR6 labels refer to the exact weights in
[IPCC AR6 WG1 Table 7.15](https://www.ipcc.ch/report/ar6/wg1/chapter/chapter-7/),
not an assertion that the comparison uses whichever accounting convention a
consumer currently requires. The numeric weights are always retained.
WattDocket's chosen comparison is a disclosed derived value:

```text
AR5 kg CO2e/MWh = (CO2 metric tonnes * 1000 + CH4 kg * 28 + N2O kg * 265)
                  / annual net generation MWh
lb CO2e/MWh = kg CO2e/MWh / 0.45359237
```

Metric tonnes must not be multiplied by the EPA short-ton factor of 2000. Gas
rates in these publisher workbooks are rounded; their implied AR5 result must
agree with the annual-component result within the explicit rounding bound
0.067 kg/MWh. The live maximum difference is 0.061450683831878905 kg/MWh. The
published AR6 annual mass reconciles within 0.010774016380310059 metric tonnes.
This precision check does not assert that the publisher's three methods agree.

November 3 validation yielded 27 comparison records. NWPP is
**582.2765418117788 lb CO2e/MWh** under WattDocket's disclosed AR5 annual-component
calculation; the publisher's annual-mass AR6 rate is 582.4147057793476 lb/MWh and
its companion comparison is 582.3727960856131 lb/MWh. EPA 2023's separate official
AR5 baseline is 635.2668276347086 lb/MWh. Differences combine year, input and
reconstruction-method effects; they are not a measured physical year-over-year
change or a 2026 facility emissions observation.

The validation state is `validated_with_disclosed_method_difference`. Any change
in pinned record/version/license, code provenance, checksum, schema, region
coverage, units or method fails closed for review. The runner may retain an older
validated record with its original capture date and report the failed attempt.
It must not silently relabel it current, hide the method discrepancy, or replace
the official geographic baseline. A weekly metadata check is sufficient; immutable
workbooks use the shared cache and all fetches share the 150-second source budget.

Live validation evidence, all regional comparison values, source hashes and the
initial rejected method assumption are retained in
`docs/verification/grid-factor-method-validation-2026-11-03.json`.

## EPA eGRID geography

The geometry adapter fetches the official `eGRID2023_Subregions` and
`eGRID2023_Multiple_Subregions` ArcGIS layers (with the year discovered from the
official workbook page), using their layer-0 metadata, independent count query
and GeoJSON output. Both layers must succeed. Future geography requiring new
field names or more than 2,000 features refuses publication until reviewed.

The primary property is `Subregion`; the overlap layer marks `MultipleSu=Yes`.
For example, the current primary layer is
<https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/eGRID2023_Subregions/FeatureServer/0>.
The other current layer is
<https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/eGRID2023_Multiple_Subregions/FeatureServer/0>.

Request WGS84 longitude/latitude, six decimal places, and a 0.001-degree server
generalization limit. Preserve all Polygon/MultiPolygon rings. The publisher's
2023 geometries contain self-touching rings and overlapping Polygon parts, so
the adapter applies an explicit, audited repair after capturing the original
bytes. Shapely 2.1.2 `make_valid(method="linework")` runs on each invalid Polygon
part, with an area-change limit of `max(1e-12, original_area * 1e-8)` square
degrees. A repair exceeding that limit refuses publication. Valid polygonal
parts are then unioned **within the same named region only**. This removes
duplicate overlapping coverage without changing the union of covered land.
Running `make_valid` on a whole overlapping MultiPolygon is avoided because its
even/odd interpretation can incorrectly remove covered overlap areas.

Each feature retains the source validity reason, repair counts, Shapely/GEOS
versions, original additive area, repaired additive area, union area, maximum
per-part area change and removed degenerate linework count. Those planar areas
are transformation checks, not physical area measurements. Raw publisher and
normalized hashes remain separate; the result is explicitly marked repaired.

After repair, try `simplify(.001, preserve_topology=True)` and verify validity
and unchanged island/hole counts. GEOS can still produce nested shells at that
tolerance. In that case retry at `.0001`, then `.00001` degree, finally retaining
the valid unsimplified geometry if necessary. Record actual per-feature
tolerances and rejected attempts. Do not flatten rings into additive areas or
drop small islands/holes to hit a file-size target.

The two possible generalization steps can compound. The binding code must use
at least a **0.0021-degree boundary guard**, refuse points in the overlapping
subregion layer, and refuse a feature marked `geometry_valid=false`. A failed
geometry fetch may retain the prior verified geometry with its original dates;
it does not authorize guessed bindings. EPA explicitly requires utility
information to resolve overlap areas:
<https://www.epa.gov/egrid/egrid-mapping-files>.

Return `data = {geojson, overlap_geojson, data_year, tolerance_degrees,
server_tolerance_degrees, boundary_buffer_degrees, coordinate_precision_degrees,
sha256, overlap_sha256, normalized_sha256, normalized_overlap_sha256,
normalized_bytes, source_urls, checked_at, binding_method, ...}`.

The live November 3 run validated all 27 primary features and the overlap
feature. It repaired 1,560 invalid Polygon parts; the largest per-part relative
area change was `2.43e-14`, well below the `1e-8` gate. ERCT, NEWE and the
overlap layer required a `.0001` local tolerance after `.001` yielded nested
shells. The complete result is 23.0 MB JSON / 5.10 MB gzip; normalization took
33.6 seconds after retrieval. The geometry publication date remains
`2025-01-17T03:32:24Z`, separate from the 2026 retrieval date. The publication
runner should allocate at least 2 GB memory, allow 120-second ArcGIS requests,
archive the approximately 74.6 MB source geometry privately and gzip public
JSON. This larger validated payload is preferable to discarding small islands
or holes for size alone.

## EIA-930 national hourly electricity

- Discover downloadable files from
  <https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_File_List_Meta.csv>.
- Fetch each advertised `EIA930_BALANCE_YYYY_Jan_Jun.csv` or
  `EIA930_BALANCE_YYYY_Jul_Dec.csv` needed for the rolling seven-day window.
  Crossing January 1 or July 1 requires both files. Reject HTML bodies, schema
  loss, truncated rows, invalid timestamps and conflicting duplicate hours.
- Resolve BA names through the publisher's keyless respondent registry:
  <https://www.eia.gov/electricity/930-api/respondents/data>.
- Select **original reported** demand, generation, fuel mix and total
  interchange. The source also includes imputed and adjusted variants; those
  stay in the captured raw CSV and never silently fill the selected stream.
- Keep forecast demand in its own field. Future forecast rows and forecast-only
  rows do not advance an observation watermark. Measure freshness from the last
  non-null reported measurement, not the last row or last download timestamp.
- Parse `UTC Time at End of Hour` as UTC and subtract one hour for the interval
  start. Local timestamps do not participate in joins. Negative storage values
  and zero observations are preserved; blank fuel cells remain missing.
- Retain seven days per BA, with explicit null records for missing hours. The
  current period has 168 UTC slots regardless of daylight-saving changes.
- Refuse a national response with fewer than 50 distinct BAs containing actual
  reported observations in the seven-day window. Forecast-only BAs cannot
  satisfy this gate, and many rows from a small group of BAs cannot substitute
  for national coverage. This minimum leaves headroom below the validated 59
  observed BAs while still rejecting substantial coverage loss. The runner
  also compares observed row counts with the previous accepted release.

Return `data = {regions: [...], stream: "reported", retained_days: 7,
files: [...], method_url, intensity_available: false}`. A region contains
`code`, `name`, `source_id: "eia930"`, `unit: "MW"`, `observation_start`,
`observation_end`, `retrieved_at`, `latest_utc`, `latest_demand_mw`,
`latest_demand_utc`, `readings_count`, `reported_readings_count`,
`missing_readings_count`, `expected_readings_count`, and `readings`.

Each reading contains `interval_start_utc`, `interval_end_utc`,
`interval_minutes: 60`, nullable `demand_mw`, `generation_mw`,
`total_interchange_mw`, `forecast_demand_mw`, `fuel_mix` containing only reported
values, `missing_fuels`, `quality: reported|missing`, and `hour_convention: END`.
Empty values never become zero. The units are publisher-reported hourly mean
MW; the adapter does not relabel them MWh.

The November 3 live run returned **60 BA codes**, **10,080 normalized hourly
slots**, **8,750 slots with reported observations**, and **59 BAs with at least
one observation**. The latest original bulk measurement ended at
`2026-11-03T08:00:00Z`. Bulk files lag the dashboard's faster demand endpoint;
use a 48-hour data freshness threshold for this adapter, not a claim of live
hourly demand. EIA documents the reporting delays and data limitations here:
<https://www.eia.gov/electricity/930-content/about.html>.

### Hourly carbon intentionally has no substituted values

The keyless EIA dashboard API exposes `CO2.CER` and `CO2.GER` series, but the
October–November 3, 2026 responses for ERCO/CISO/PJM were null or isolated zero
values despite recent dates in the metadata endpoint. Metadata timestamps alone
are not evidence that emissions measurements exist. These series do not feed
production carbon claims. Neither the old hand-compiled fuel factor set nor a
state-level guess fills this gap.

## ERCOT five-minute generation

Fetch <https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json>.
Store the publisher's explicit-offset interval labels in UTC and retain
`lastUpdated` independently from retrieval time. Preserve current and previous
operating days; the clock change can produce 276, 288 or 300 expected readings
in a completed local day. The current day is expected only through its latest
published interval.

Keep generating fuels in `fuel_mix` and storage separately. When both storage
components exist, their sum is authoritative; the net storage aggregate is not
added a second time. Null fuel readings remain missing, and total generating MW
is null if a required generating fuel is missing. Reject unknown fuel categories
rather than silently discarding their contribution.

Return `data = {regions: [ERCO_region], intensity_available: false}`. The region
uses the same core shape as EIA but `source_id: "ercot"` and five-minute
intervals. `days` carries expected/published interval counts and missing UTC
timestamps. `quality` can be `reported`, `partial` or `missing`; gap rows have
null numbers. Raw telemetry stays in **MW**, and unsupported hourly carbon
remains null. These are not facility meter readings.

The live validation retrieved 448 reported intervals through
`2026-11-03T19:20:00Z`. November 2 contained a real missing interval at
`2026-11-02T16:40:00Z`; it was retained as a null gap, not interpolated. The
publisher's explanation of the feed is at
<https://www.ercot.com/gridmktinfo/dashboards/fuelmix>.

## Validation

`core/tests/test_production_grid.py` covers official-edition discovery, workbook
vintage, half-year boundaries, UTC hour-ending conversion, null versus zero,
negative storage, forecasts, duplicate conflicts, schema/HTML failures,
national coverage gates, explicit missing slots, DST fallback, unknown ERCOT
fuels, geometry truncation, overlap-layer completeness, and preservation of
holes and tiny islands, overlapping-part union semantics, area-changing repair
refusal, and valid unsimplified fallback when GEOS simplification fails.

The adapters were also run against the actual publishers on
November 3, 2026. Live-source evidence is distinct from offline fixture tests;
no source availability is inferred from a unit test.
