# Permit reading

## Why this exists

Most of what an air permit obliges a data centre to do is in a PDF: how many
hours its diesel generators may run, how much fuel it may store, what it must
test and when. That document is public. Almost nobody reads it, because it is
200 pages of regulatory prose with the operative numbers scattered through it.

Summarising it is the obvious approach and the wrong one. A summary of a permit
is a claim about a legal instrument. If the summary is subtly wrong, the reader
has no way to tell, and the tool has licensed an error.

## The rule

**The model reads. Python verifies. Python derives the page.**

1. The model is given page text and asked for quotes — exact substrings, not
   paraphrases, and never a page number.
2. Each quote is searched for in text this module extracted itself from the PDF
   bytes on disk. Whitespace is normalised on both sides, nothing else.
3. The page and line number are whatever the search landed on. They are
   properties of the document, not of the model's output.
4. A quote that is not found is **rejected and reported**. It is never
   downgraded to "the model says page 41" and it never reaches a card.

## What it looks like on a real permit

Texas Commission on Environmental Quality, PBR registration 164110 —
Amazon Data Services, ALE60, 7400 Potranco Road, Bexar County. Two pages.
sha256 `017f85b531cf2d07…`

`muse-spark-1.3` proposed 6 obligations, all 6 verified verbatim, 0 rejected,
10.0 s:

| page | line | obligation |
|---|---|---|
| 1 | 30 | 2 × 40,000-gallon diesel storage tanks |
| 1 | 33 | Bexar County — serious nonattainment, 2015 8-hour ozone (0.070 ppm) |
| 1 | 35 | no single generator over 100 hr/year for non-emergency use |
| 1 | 67 | 2,025 hrs/yr across all generators (revised limit) |
| 2 | 14 | emergency engines ≤ 10% of the normal annual operating schedule |
| 2 | 131 | engines based on a maximum of 2,025 hours per year |

Every one of those page/line pairs was checked against the raw page text.

## The finding that justifies the design

The permit contradicts itself. Page 1 authorises a *"maximum operational
schedule of 2,055 hours per year"*; the emissions-basis note, on page 1 and
again on page 2, says *"a maximum of 2,025 hours per year"*. Thirty hours apart,
in a two-page document, with no explanation.

A summariser picks one and sounds certain. `detect_value_conflicts()` reports
both, with both citations, and says so on the card. That is the product's whole
argument in miniature: a cited contradiction is more useful than a confident
average.

## Coverage and limits

- Works on text-layer PDFs. A scanned permit needs OCR first; the module refuses
  rather than returning empty text as if the document said nothing.
- `facility_identity()` reads company, county, city, address and registration
  from the document. It is regex over the first page, so an unusual layout
  yields `{}` — which is honest, and is what happened on the 214-page Compass
  application packet.
- Quotes spanning a page break cannot be verified against one page and are
  rejected. This is deliberate: the alternative is a page number that is half
  wrong.
