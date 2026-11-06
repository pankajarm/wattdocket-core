# Data provenance

WattDocket combines public source records while preserving their reporting boundaries, dates, units, citations, and uncertainty. The current bundled release is identified by [`site/data/current.json`](site/data/current.json). Its hash-checked `data-status.json` is the source of truth for coverage, retrieval dates, and source health in that release. Counts in a downloaded snapshot are not a claim of complete US coverage.

## Reproduce the public evidence

Follow [Data snapshots](docs/DATA-SNAPSHOT.md) to verify or refresh the bundled release. The manifest names every published object and its decoded SHA-256. Source drawers and individual observations retain their publisher links, citations, reporting periods, and available retrieval or content-hash metadata.

The repository includes the public application data and evidence objects used by the selected release. It does not include every original upstream file, an unfiltered collector archive, credentials, or private operational logs. Public record links may lead to a publisher-hosted PDF, spreadsheet, portal, or API; availability and access terms can change.

Files under `data/`, test fixtures, and older static API artifacts can be historical build inputs or regression fixtures. They are not a substitute for the selected production manifest, and their dates and scope must not be presented as current coverage.

## Sources in the bundled release

The following catalogue describes the source families present in the November 2026 public release. Read the selected release's source-status records for its exact editions and current, stale, partial, or unavailable state.

| Source family | Original publisher and entry point | Use and important limits |
| --- | --- | --- |
| Facility and air-program records | [US EPA ECHO](https://echodata.epa.gov/echo/echo_rest_services.get_facilities?p_ncs=518210) | Industry-code listings and regulatory identifiers. An industry code does not independently verify facility type, physical location, electricity demand, or water use. |
| Community facility mapping | [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), queried through Overpass | Discovery and mapped locations. Community records are not regulator verification and are not merged into EPA records without reviewed identity evidence. |
| Virginia permits | [Virginia DEQ issued air permits for data centers](https://www.deq.virginia.gov/news-info/shortcuts/permits/air/issued-air-permits-for-data-centers) | Document identity, quoted conditions, and reviewed document history. A fresh download alone does not establish that every condition remains effective. |
| Virginia site identity | [Virginia DEQ public site layer](https://gisdata.deq.virginia.gov/arcgis/rest/services/public/EDMA/MapServer/294) | Physical addresses, ICIS identifiers, and reported program status. No operating power, water, or emissions are inferred. |
| Texas air permits | [Texas Commission on Environmental Quality](https://www.tceq.texas.gov/permitting/air/air_status_permits.html) | Permit documents and attributed conditions. Drafts and technical reviews retain their document type and are not described as issued operating limits. |
| Annual regional emissions factors | [US EPA eGRID](https://www.epa.gov/egrid), including the pinned 2023 workbook and subregion geometry | Annual location-based accounting factors and geographic context. They are not hourly or marginal emissions rates; a geographic match is not proof of the serving utility. |
| Independent annual comparison | [Cornerstone eGRID 2024 reconstruction on Zenodo](https://zenodo.org/records/18968658) | A separately labeled reconstruction from public EPA code, not an EPA-published eGRID release. Published greenhouse-gas accounting variants and the explicit recalculation remain distinct. It does not silently replace the official baseline. |
| Hourly regional grid data | [US EIA-930 Grid Monitor](https://www.eia.gov/electricity/gridmonitor/) | Reported demand and generation with explicit time handling and gaps. Imputed or adjusted streams do not replace reported values. Unsupported hourly carbon values are withheld. |
| ERCOT fuel generation | [ERCOT fuel-mix feed](https://www.ercot.com/api/1/services/read/dashboards/fuel-mix.json) | Regional generation in MW; storage remains separate and missing intervals remain gaps. No facility load or unsupported hourly carbon factor is inferred. |
| Reported direct combustion emissions | [US EPA Greenhouse Gas Reporting Program](https://www.epa.gov/ghgreporting/ghg-reporting-program-data-sets) | Selected Subpart C regulatory reporting entities. These reports are not electricity Scope 2, lifecycle footprints, continuous measurements, or a complete operator inventory. |
| Meta annual disclosures | [Meta 2025 Environmental Data Index](https://sustainability.atmeta.com/asset/2025-environmental-data-index/) | Reported campus or location electricity, water withdrawal, and emissions. The source may use estimates where activity data is incomplete; individual totals are not allocated to EPA records. |
| Google annual water disclosures | [Google 2026 Environmental Report](https://sustainability.google/reports/google-2026-environmental-report/) | Reported 2025 location water quantities, with methodology and exclusions retained. Named locations can contain multiple buildings; a row's metered-versus-estimated share is not established. |
| Massachusetts building reports | [Massachusetts Large Building Energy Reporting](https://www.mass.gov/info-details/large-building-energy-reporting-results) | Selected annual building reporting entities. Purchased-grid energy reported in kBtu is not MW demand. State-calculated GHG emissions are not direct emissions measurements. |
| New York City building reports | [NYC Building Energy and Water Data](https://data.cityofnewyork.us/Environment/NYC-Building-Energy-and-Water-Data-Disclosure-for-/5zyy-y8am) | Whole reporting properties, potentially with mixed uses. Optional peak-demand entries do not establish a coincident campus maximum or a complete interval series. |
| Seattle building reports | [Seattle Building Energy Benchmarking](https://data.seattle.gov/Built-Environment/Building-Energy-Benchmarking-Data-2015-Present/teqw-tu6e) | Whole reporting properties classified by the publisher. Values are not isolated IT or data-center-only quantities. |
| Nebraska demand reports | [Nebraska Data Center Task Force](https://dwee.nebraska.gov/data-center-task-force) | Selected reported maxima with their campus or property scope, including an explicitly labeled cryptocurrency-mining property. No statewide census or interval meter feed is implied. |
| Historical C3 power | [Idaho National Laboratory C3 dataset](https://nrds.inl.gov/dataset/c3_datacenter_hourly_power_usage) | Publisher-reported hourly power, retaining unit and timestamp interpretation limits. Missing cells remain null; no live telemetry or whole-facility allocation is inferred. |
| Historical ESIF samples | [National Laboratory of the Rockies data catalogue](https://data.nlr.gov/submissions/300) | Historical measured samples. Aggregates are withheld when coverage is inadequate. This is not a live feed, annual energy observation, or whole-facility meter. |

Reviewed links between disclosures and inventory records are a separate evidence layer. Their supporting documents are retained in the release's identity evidence. A coverage relationship does not mean the two reporting boundaries are equal, and it does not justify dividing a campus total among inventory records.

## Interpretation rules

- **Missing is not zero.** Missing, withheld, stale, failed, and not-published values remain distinct. A reported or computed zero remains a real zero.
- **Capacity is not consumption.** Generator nameplate capacity and permit allowances do not establish actual electricity demand or annual activity.
- **A scenario is an assumption.** Historical-grid calculations use explicit load and utilization inputs; they do not establish a facility's measured use or emissions.
- **Periods and boundaries stay attached.** A campus, property, meter series, operator, permit, regulatory entity, and regional grid are different subjects.
- **Retrieval is not current effect.** A recent download does not make an older disclosure a current measurement or a permit the latest effective authorization.
- **Evidence outranks prose.** A model explanation is a reading aid. Source records, cited qualifications, deterministic calculations, and validation results remain inspectable.

## Attribution and reuse

The project's [Apache-2.0 license](LICENSE) applies to its code. It does not relicense third-party datasets, reports, PDF documents, map tiles, or publisher trademarks. [NOTICE](NOTICE) preserves this distinction.

OpenStreetMap-derived data is **© OpenStreetMap contributors**, available under the [Open Data Commons Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/). Keep the attribution and comply with the applicable database and derived-data requirements when using or redistributing it. See the publisher's [copyright and license guidance](https://www.openstreetmap.org/copyright); map-tile services also have separate usage policies.

Operator reports remain the copyright of their publishers. For example, the Google source metadata labels its use as an attributed factual extraction and retains the original report URL, page references, and PDF hash. Government, municipal, laboratory, and independently reconstructed datasets retain their own notices and source-specific terms. Public access alone is not a blanket redistribution license. Preserve the terms supplied with each source and check the original publisher before distributing a new dataset or archive.

For a suspected data or attribution error, open a [data correction issue](https://github.com/pankajarm/wattdocket-core/issues/new?template=data_correction.yml) with the affected record and original source. Use the [security reporting process](SECURITY.md) if the issue exposes private information or creates a security risk.
