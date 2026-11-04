# Operator water disclosures

`core/wattdocket/production/sources_operator_reports.py` implements
`collect_google_water_disclosures(fetcher, now=...)`, source ID
`google_water2025`. It returns a `SourceResult` with `data.observations`,
`data.aggregates`, `data.source` and `data.validation`. The optional weekly source
has no credentials or fees and uses the existing PyMuPDF 1.26.5 dependency,
shared immutable capture/cache and 150-second fetch budget.

The selected publication is Google's
[2026 Environmental Report](https://sustainability.google/reports/google-2026-environmental-report/).
Its official report page links the
[reviewed PDF](https://sustainability.google/files/1699a2e19277e478b38a659f00cb38398a836e8c/download-1699a2e1.pdf).
This adapter covers **2025 annual water at 40 named locations worldwide**, plus
two separately retained aggregates: other locations and the data-center total.
It is a pinned reviewed report, not a claim of comprehensive operator coverage
or an automatically selected newest report. The report page identifies June
2026 as publication month; the exact day remains null.

Page 97 supplies withdrawal, discharge and consumption in **million gallons**.
The adapter retains that publisher unit without assuming a gallon convention or
converting to liters. Potable, non-potable and reclaimed withdrawal components
remain distinct where itemized. Withdrawal is potable unless otherwise noted.
Footnotes and air-cooled-location annotations are retained. The illustrative
golf-course equivalence column is checked for structural consistency but is not
published as a measurement.

Methodology on pages 87–88 permits estimates where meters or invoices are
unavailable, and defines consumption as withdrawal minus discharge. The report
does not identify the metered share for each location. Therefore rows say
**operator reported; may include estimates**. They exclude seawater and brackish
water, preserve the 2025 period, and do not claim 2026 consumption. The assurance
marks apply to the named locations and total; the Other aggregate is not marked
in the table. Limited assurance does not mean every value is a meter reading.

A location may include multiple buildings. The source supplies neither EPA IDs
nor verified meter-to-facility matches. Every row has
`facility_binding_verified: false`; no row is attached to a facility card, used
in a facility-emissions calculation, or counted as a verified facility actual.
Only publisher location names are retained; no countries, boundaries or
coordinates are guessed. Report copyright is retained with attribution; the
public output contains factual values and brief methodological paraphrases,
not a redistributed copy of the report.

Publication requires the reviewed PDF hash, linked official edition, 117-page
structure, selected methodology statements, all 40 names in expected order,
units, columns and footnotes. Named-row withdrawal minus discharge must agree
with consumption within the published rounding precision. Withdrawal components
must reconcile. Named locations plus Other must agree with the whole-million
total within 2.55 million gallons: 41 rows rounded to tenths plus a total rounded
to whole millions. A changed report, method or schema fails for review rather
than silently adapting numbers or guessing a new edition.

The November 3 live capture completed in 3.21 seconds and returned all 40
locations. The largest aggregate rounding difference was 0.2 million gallons.
The PDF was 22,939,183 bytes with SHA256
`6a23a6db212d5d7da8d887e773e98e78ee16b98eaf8d627a4e31f98b6f487ca3`.
Page 97 was visually inspected, including its column labels, total rows and
assurance marks. Reproducible numerical and capture evidence is retained in
`docs/verification/google-water-disclosures-2026-11-03.json`.

`core/tests/test_production_operator_reports.py` verifies genuine PDF extraction
with a synthetic 117-page fixture, scope preservation, water measures remaining
distinct, lack of inferred facility joins, and rejection of changed hashes,
methods, periods, units, coverage, columns, nonfinite values and bad arithmetic.

## Next bounded candidate

Meta's [2025 Environmental Data Index](https://sustainability.atmeta.com/asset/2025-environmental-data-index/)
reports 2024 electricity in MWh by named campus (page 6), and withdrawal in
megaliters (page 9). For example, Altoona reports 1,585,392 MWh and 242 ML.
Its page 18 methodology permits water estimates when direct data is unavailable.
This is a credible future source for separate operator-campus disclosures; it
is not implemented here and does not authorize matching those totals to individual
EPA facility records. Campus scope and actual-versus-estimated evidence require
the same explicit treatment before any facility attachment.
