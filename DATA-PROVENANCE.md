# DATA PROVENANCE

Every number in WattDocket resolves to a row here, and every row here resolves to a URL with a sha256. If a dataset is down, the product degrades in a named, visible way — it does not guess.

**Legend for access:** `KEYLESS` = no credentials; `FREE-KEY` = free registration required; `PAID` = not used.

---

## 1. Facility identity and compliance

| # | Dataset | Publisher | Access | Cadence | Coverage | Feeds | Failure mode | Fallback |
|---|---|---|---|---|---|---|---|---|
| 1 | **ECHO Air — `air_rest_services.get_facility_info`, NAICS 518210** | US EPA | KEYLESS | continuous | **479 US facilities, all geocoded** (401 operating · 20 planned · 3 under construction · 35 closed), 2026-10-27 snapshot | `Facility`, compliance status, diesel permits. **Penalty money is a results-level aggregate** ($570,793.58 across 23 facilities, 5-year window) — the row-level `FacPenaltyAmount` field is empty for **0/479** rows, so per-facility penalty amounts must be fetched individually, never inferred from the CSV. | API shape change; paging quirks (returns counts and rows in different endpoints) | Frozen CSV in `data/`, refreshed weekly; ship the snapshot in the repo |
| 2 | ECHO detailed facility report (per facility) | US EPA | KEYLESS | continuous | per-facility | permit numbers, inspection dates, enforcement | HTML change | use row-level fields from (1) |
| 3 | ICIS-Air / state synthetic-minor permits | EPA + states | KEYLESS (varies) | varies | uneven, state-dependent | generator caps (hours/yr, MW) | broken links, PDF-only | `NOT_PUBLISHED` state, with the agency link |
| 4 | Air permits (state DEQ) — PDFs | State agencies | KEYLESS | on filing | Virginia, Texas deep | `Citation` rows: engine counts, hour caps, cooling towers | scanned PDFs, no text layer | OCR pass; if quote cannot verify → discard the fact |
| 5 | GHGRP (Envirofacts) | US EPA | KEYLESS | annual | **only ~2% of data centers** (diesel under 25kt threshold) | reported Scope-1 combustion | empty result misread as zero | **INV-8**: empty renders "not reported" |
| 6 | PeeringDB facilities API | PeeringDB | KEYLESS | continuous | **1,378 US facilities with lat/lon** | facility discovery, coordinates | community data quality | ECHO (1) is authoritative where they disagree; show both |

| 6b | **US Census Geocoder** | US Census Bureau | KEYLESS | continuous | US addresses | address → lat/lon via authoritative address ranges (**primary geocoder**, verified 2026-10-28) | occasional maintenance windows | OpenStreetMap Nominatim (secondary, rate-limited, needs a UA) |

## 2. Grid and carbon

| # | Dataset | Publisher | Access | Cadence | Coverage | Feeds | Failure mode | Fallback |
|---|---|---|---|---|---|---|---|---|
| 7 | **eGRID 2023 workbook (`egrid2023_data_rev2.xlsx`)** | US EPA | KEYLESS | annual | 27 subregions; per-plant, per-generator | subregion CO₂e lb/MWh (frozen extract `data/egrid_subregion_rates.json`, cited by its sha256), plant-level cross-check. *Not* the source of `FactorSet` `us-avg-2023.1`, which is a hand-compiled national table (see `artifacts/factor_set.json`) | file moved (EPALinks break yearly) | pinned copy in the repository + sha256 in `method` |
| 8 | **eGRID 2023 subregion polygons (ArcGIS FeatureServer)** | US EPA | KEYLESS | annual | all 50 states | address → subregion via point-in-polygon | service throttling | ship the GeoJSON with the app |
| 9 | **ERCOT fuel-mix dashboard JSON** | ERCOT | KEYLESS | 5-minute | ERCOT only | `IntervalReading`, hourly intensity | schema change; occasional gaps | retain last good snapshot; mark the day `stale` |
| 10 | EIA-930 hourly electric grid monitor | US EIA | KEYLESS (bulk; six-month CSV verified reachable, 65 columns) | hourly | all US balancing authorities | hourly load/gen/interchange nationwide | **timestamps are hour-ENDING**; 4 storage classes; two known header typos in the CSV | parse with an explicit hour-ending flag (INV-3); bulk monthly file + daily incremental |
| 11 | CAISO OASIS | CAISO | KEYLESS | 5-minute | CAISO | intensity for California | XML zip quirks | EIA-930 (10) |
| 12 | PJM Data Miner 2 | PJM | FREE-KEY (**confirmed**: `api.pjm.com` returns 401 unkeyed, 2026-10-28) | 5-minute | PJM | intensity for Virginia | keyed only; no anonymous access | **EIA-930 (10)** for the demo; do not block on PJM |
| 13 | Open Grid Emissions (OGE) | Singularity Energy | KEYLESS | annual/hourly | US | **SETTLED** intensity, replacing PROVISIONAL | release lag | keep `PROVISIONAL` label — never silently upgrade |
| 14 | EPA CAMPD / CEMS | US EPA | KEYLESS | hourly, ~1 quarter lag | large emitters | settle intensity where available | lag makes it unusable for "today" | OGE (13) |

**Emission factor sets (must be versioned and one click away):** the fuel-mix → intensity conversion depends on a factor table. Three plausible tables move the *level* ~20% while the *ratio* stays ~1.75×. We publish our table as a `FactorSet` with a sha256, and label every derived number `PROVISIONAL` until (13) or (14) settles it.

## 3. Water, land, weather

| # | Dataset | Publisher | Access | Cadence | Coverage | Feeds | Failure mode | Fallback |
|---|---|---|---|---|---|---|---|---|
| 15 | Operator water disclosures (e.g. per-site tables) | Hyperscalers | KEYLESS | annual/irregular | a handful | `WaterState.DISCLOSED` | disappears without notice | archive a snapshot with sha256 at capture time |
| 16 | WRI Aqueduct water stress | WRI | KEYLESS | periodic | global, sub-basin | context for the water band | version drift | pin a version |
| 17 | USGS NWIS stream gauges | USGS | KEYLESS | 15-minute | US | cooling-source context | station turnover | nearest-station search |
| 18 | Building footprints (Overture / Microsoft) | Overture Maps | KEYLESS | quarterly | global | land area, rooftop area cross-check | licence terms change | compute from imagery |
| 19 | Hydrography (NHD) | USGS | KEYLESS | periodic | US | discharge proximity | — | omit |
| 20 | Open-Meteo (wet-bulb) | Open-Meteo | KEYLESS | hourly | global | cooling-water sensitivity | rate limits | cache 30 days |

**Water rule (INV-9):** no US state effectively requires facility-level *operational* water reporting. Water renders as `DISCLOSED` (with source), `MODELLED_BAND` (cooling type × MW × climate), or `NOT_PUBLISHED`. There is no scalar.

## 4. National priors

| # | Dataset | Publisher | Access | Feeds |
|---|---|---|---|---|
| 21 | LBNL/DOE 2024 US Data Center Energy Usage Report | LBNL / DOE | KEYLESS | utilization prior 0.45–0.80, PUE 1.12–1.45, growth scenarios (176 TWh in 2023 → 325–580 TWh by 2028) |
| 22 | EPA AVERT / marginal emission methodology | US EPA | KEYLESS | marginal factors for load-shift levers |

## 5. Context only — never used as evidence

| Dataset | Why it is a trap |
|---|---|
| Company ESG scores / league tables | Aggregate rankings with opaque methods; not facility evidence |
| Crowdsourced report maps | Useful signal, zero verification; we link to them, we never cite them as fact |
| AirNow monitor readings | A monitor near a facility invites a causation claim we cannot support |
| Ask the Grid derived datasets | Their packaging, not the primary chain. We link to the publisher, not the reseller |

## 6. What we refuse to ship

- Any number without a receipt (INV-7).
- Any water scalar (INV-9).
- Any intensity labelled settled when it is provisional (INV-6).
- Any generated image in an evidence surface.
- Any "violates permit" phrasing; only "permit allows X, record reports Y".
- Any scraped copy of a competitor's dataset.

## 7. Refresh model

| tier | cadence | mechanism |
|---|---|---|
| Immutable (eGRID annual, factor sets, archived PDFs) | yearly / on capture | content-addressed snapshots; a new file is a new key |
| Daily (ECHO, EIA-930, PeeringDB) | daily | scheduled collector → one partition per source per date |
| Live (ERCOT/CAISO 5-minute) | 5 min | cached snapshots with `as_of`; a stale feed is labelled, never interpolated |

Every dataset row carries `retrieved_at`, `sha256`, and `publisher_url`. A failure in any single source degrades exactly one field, and that field says so on screen.
