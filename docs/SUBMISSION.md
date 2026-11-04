# Hackathon submission text

Paste-ready text for the Meta Global AI Developer Hackathon form. Numbers are
from the submitted snapshot; re-check them on the live site's coverage page
before submitting.

- **Event:** Meta Global AI Developer Hackathon, Indie track
- **Deadline:** 2026-11-04, 9:00 PM ET
- **Live site:** https://wattdocket.com
- **Code:** https://github.com/pankajarm/wattdocket-core (Apache-2.0)
- **Video (3 minutes):** _add the link after upload_

---

## Project name

WattDocket

## Tagline (≤ 200 characters)

Public records on US data centers, cited to the line: permits, grid context and
operator disclosures, with every number traceable and every gap labelled.

## Inspiration

Data centers are the fastest-growing load on the US grid, and the arguments
about them run on numbers nobody can check. Almost no facility publishes its
consumption. Residents, reporters and councils get a headline figure with no
source. We wanted the opposite: a number you can click, that shows you the
line in the public record it came from, and that tells you when the record is
silent.

## What it does

WattDocket is an evidence atlas for data center facilities in the US. Start on
the map or search by address, facility name or EPA ECHO id, then open a card:

- **Source records:** EPA ECHO air-program status and identifiers, Virginia DEQ
  registration links and selected Texas TCEQ documents, each with the quoted
  passage, document scope, hashes and retrieval date.
- **Grid context:** the facility's EPA eGRID subregion and official annual
  factors, EIA-930 hourly demand and generation for 60 balancing authorities,
  and ERCOT five-minute fuel mix. Regional data never pretends to be a meter.
- **Operator disclosures:** reported annual electricity, water and emissions
  for named campuses and buildings (Meta, Google, Massachusetts, EPA GHGRP),
  kept separate from inventory records unless a reviewed source links them.
- **Reference scenarios:** an editable, clearly hypothetical load calculator.
- **Operator view:** the same evidence with CSV and JSON exports behind an
  optional account. All data is public; an account adds no private data.

Where the record is silent (facility demand, water use, verified emissions),
the card says "not available" instead of filling the blank.

## How we built it

- **Rule zero: the model reads and cites; it never computes.** Every digit on
  screen comes from deterministic Python reading a public record.
- **Meta Muse Spark (`muse-spark-1.3`) reads permits.** It receives a permit's
  operative pages and returns each obligation with a page, line and verbatim
  quote. Python checks every quote against the document and rejects anything
  it cannot find. On a Texas TCEQ permit, 6 of 6 proposed obligations verified.
- **Muse Spark writes plain-language explanations.** A guarded function gives
  it only server-owned facts for one facility. Any numeral not in those facts
  rejects the reply and the reader gets the deterministic template. A
  fail-closed daily limit caps paid calls. Each release publishes those facts
  for every ECHO card; OpenStreetMap records without reviewed facts show a
  labelled template instead.
- **Publication:** a scheduled collector archives every source response,
  validates identities and evidence, and publishes immutable releases. The
  browser pins one release per session.
- **Stack:** Python engine; React + Vite + TypeScript with a Leaflet /
  OpenStreetMap atlas; PyMuPDF for permit text. The live site is hosted
  separately from this open-source repository.

## Challenges we ran into

- **A value and its citation must come from the same match.** Searching the
  document for one and the page for the other once quoted 11 hours as 19.
- **The model invented a negative from an absence.** A missing field became
  "not published" in the model's sentence. The fix: never describe something
  as unpublished unless a fact says so, and check every numeral.
- **Reasoning tokens ate the output budget.** At default effort the reply was
  empty; a bounded budget with minimal reasoning effort fixed it.
- **Permits contradict themselves.** A Texas permit states 2,055 hours in one
  place and 2,025 in another. We report both.
- **Records are not buildings.** 3,036 source records are not 3,036 sites, and
  name-only matches stay candidates. The site says so.

## Accomplishments that we're proud of

- 3,036 source records (969 EPA ECHO, 2,067 OpenStreetMap), 765 with
  checked grid context, and 2,234 locations withheld rather than guessed.
  The 479 ECHO facility records are one layer within that total.
- 198 permit documents listed, 185 readable; 68 records with quoted
  conditions.
- 234 reported annual observations across 73 reporting entities.
- 650 Python tests pass (1 skipped) and 95 web tests, including the invariants
  that keep the model away from arithmetic.

## What we learned

An honest "we don't know" is a feature people can use. The hard part of an
evidence product is not the estimate; it is keeping custody of where each
number came from, and refusing to fill a blank with something plausible.

## What's next for WattDocket

- More state permit registries beyond Virginia and Texas.

## Built with

`meta-muse-spark` · `meta-model-api` · `python` · `react` · `typescript` ·
`vite` · `leaflet` · `openstreetmap` · `pymupdf` · `epa-echo` · `egrid` ·
`eia-930` · `ercot`

## Screenshots

Retake the submission screenshots from https://wattdocket.com before uploading
so they match the live site.
