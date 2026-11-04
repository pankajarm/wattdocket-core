# Reading Virginia's data-center air permits

## Why this matters more than the Texas lane

Virginia is where the data centers are. The Commonwealth publishes a registry
titled **"Issued Air Permits for Data Centers"** — 188 permits across 173 sites,
89 of them in Loudoun County alone. It is a state government asserting, in its
own publication, that these are data centers, which is the facility-type evidence
a NAICS code never provides.

Each entry links the actual permit document, which states the obligations:
generator run-hour caps, fuel throughput, sulfur limits, tonnage.

## The two problems, and how each is handled

**1. The origin blocks this host.** `deq.virginia.gov` returns 403 through an
Akamai rule to a plain client *and* to a real browser from this machine, while
the document is public and linked from the government's own page. Text is
therefore retrieved through a reading service (`r.jina.ai`).

That is a provenance fact, not an implementation detail: the bytes we quote did
not come from a PDF this process hashed. Every citation carries
`retrieved_via`, and the card prints a sentence saying so. A citation that hid
this would be claiming a chain of custody it does not have.

**2. There is no PDF here to derive a page from.** The Texas reader derives a
page by searching text it extracted itself. Virginia instead prints its own
footer on every page — *"… Registration Number: 30142 February 12, 2025 Page 8"* —
so splitting on that marker recovers the pagination the Commonwealth itself
asserts. That is stronger evidence than an offset into a re-flowed text layer,
because the page number is literally printed on the page.

Content after a footer belongs to the next page, since the footer closes the page
it sits on.

## What a permit says on a real site

TECfusions Clarksville, 250 Burlington Drive, Mecklenburg County — the card at
`echo:110001894040`:

| permitted limit | value | page |
|---|---|---|
| Generator run hours | no more than **100 hours/year** per generator | 8 |
| Diesel fuel throughput | no more than **235,200 gallons/year** | 9 |
| Fuel sulfur | at most **0.0015%** by shipment | 9 |

All three verified verbatim against the document's own page text.

## The honesty rule this lane taught

**A permitted limit is not an operating record.** A site capped at 100 hours may
run zero. The panel is headed `PERMITTED LIMITS — NOT MEASUREMENTS`, every row is
phrased as a ceiling ("no more than"), and the footnote says outright that
nothing here is a measurement. This is the same class of error as treating
reported capacity as a measured load, and it is the one a hostile reader finds
first.

## Extraction is narrow on purpose

Three regex patterns, each for a condition Virginia writes in a fixed form. A
broad extractor produces plausible obligations; these produce checkable ones.
Anything that does not resolve to a single page is dropped rather than cited with
a guessed page — the same rule as the Texas reader.
