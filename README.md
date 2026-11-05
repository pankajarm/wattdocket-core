# WattDocket

**Public records on US data centers, cited to the line.** WattDocket is an evidence atlas for data-center facilities: permits, grid context and operator disclosures, with every number traceable to a source and every gap labeled.

Built for the **Meta Global AI Developer Hackathon, Indie track**.

- **Live site:** [wattdocket.com](https://wattdocket.com)
- **This repository:** the open-source hackathon submission, `pankajarm/wattdocket-core`

WattDocket does **not** claim to know a facility's actual electricity demand, water use or annual emissions when the public record is silent. A reference load is a chosen assumption, clearly labeled as a scenario — never presented as the facility's real consumption.

## Watch the demo

[![Watch the WattDocket demo — 2 minutes 52 seconds](https://wattdocket.com/media/wattdocket-demo-31ba55c1-poster-v2.jpg)](https://wattdocket.com/media/wattdocket-demo-31ba55c1.mp4)

**[Watch the 2:52 demo](https://wattdocket.com/media/wattdocket-demo-31ba55c1.mp4)** · [Watch with captions and chapter shortcuts](https://wattdocket.com/)

See reported data, quoted permit conditions, a live Meta Muse Spark explanation, and the historical grid calculator.

## What it does

- **Facility atlas and public cards.** Search by address, facility name or EPA ECHO id. Each card shows attributed source records, EPA AIR program status and identifiers, strict county/registration links, and available document evidence. Name-only matches stay candidates.
- **Source drawers.** The cited quotation or source artifact, document scope, calculation steps, hashes, reporting periods and retrieval information. Drafts, cached evidence and failed refreshes remain labeled.
- **Grid context.** Official EPA eGRID annual factors, a separate independent regional comparison, EIA-930 reported hourly demand and generation, and ERCOT five-minute fuel generation. Regional data never pretends to be a facility meter.
- **Reference scenarios.** An editable historical-grid calculator with a 10 MW starting preset and low/base/high utilization. The inputs and results are hypothetical.
- **Operator view.** The same public evidence with CSV and JSON exports. An account adds no private data; all application data is public.
- **Learning and reported data.** Guided explainers (Understand) cover electricity, water, emissions, generator permits, the regional grid, electricity bills, community evidence and a Virginia research guide, each tied to cited examples. The Reported data explorer searches operator and municipal disclosures by measure, year, publisher and reporting scope, and facility cards link to the matching disclosure locations with scope and gaps labeled. Reported totals describe the named campus, building or reporting location for their year; they are not allocated to individual inventory records.

## How WattDocket uses Meta Muse Spark

WattDocket is built on **Meta Muse Spark through the Meta Model API**. One rule runs through every model path: **the model reads and cites; it never computes.** Every number on screen comes from deterministic Python reading a public record, and the model's output is checked before anything is shown.

### Live card explanations

- `api/wattdocket_api/explain.py` — the local composer. It gives Muse Spark only server-owned facts for one facility and asks for two plain sentences.
- `api/lambda_explain/handler.py` — the optional deployed explain handler. It accepts only facility ids in its bundled registry and rejects any numeral that is not present in the server-owned facts. A rejection, timeout or `MODEL_OFF=1` returns the deterministic template instead.
- `core/wattdocket/production/explain.py` — builds the per-card fact set and deterministic summary that the model is allowed to use.
- `web/src/components/ExplainPanel.tsx` and `web/src/api.ts` — the card UI and its fail-closed client call. Without `VITE_EXPLAIN_URL`, the card shows the deterministic summary alone.

The model never sees client-supplied text, and a reply containing an unbound number is replaced by the summary.

### Permit reader

- `scripts/read_permit_with_muse.py` sends a permit's operative pages to `muse-spark-1.3` and asks what the permit obliges.
- The model must return each obligation with a page, line and **verbatim quote**.
- `core/wattdocket/permits.py` checks every quote against the document text and rejects anything it cannot find. A citation survives; an assertion does not.
- `artifacts/permit_read.json` records a run on a Texas TCEQ permit: 6 obligations proposed, 6 verified.

The application uses Meta Muse Spark only. No other AI model or coding tool is part of the runtime.

## Development tooling

WattDocket was developed and released with **Meta Muse Code CLI (`muse`)** for repository work, code review, test runs and release synchronization. Muse Code is development tooling, not part of the runtime; the deployed product uses only Meta Muse Spark through the Meta Model API.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `core/wattdocket/` | Deterministic calculation engine: units, timing, grid, water, estimates, permits, evidence |
| `core/wattdocket/production/` | Source adapters, validation, release building and card/explain payloads |
| `core/tests/` | Unit, invariant, evidence and failure-injection tests |
| `tests/` | API, explain, sharing and scheduling tests |
| `api/wattdocket_api/` | Local FastAPI service for development |
| `api/lambda_explain/` | Optional serverless explain handler |
| `web/src/` | React + TypeScript UI, atlas, cards, source drawers and operator view |
| `web/scripts/` | Frontend regression tests |
| `data/` | Source datasets, permit PDFs and frozen inputs used by the build |
| `site/api/`, `site/data/` | Checked-in static API/data snapshot used by local development |
| `scripts/` | Data build, calibration and permit-reading utilities |
| `docs/` | Submission text, permit notes and source research |

`deploy/`, the built `site/app/` bundle and the live-site infrastructure are intentionally **not** part of this repository.

## Setup and run

Requirements: Python 3.11+ and Node.js 20+.

```sh
python3 -m venv .venv
.venv/bin/pip install -e "core[dev]"

cd web
npm ci
npm run dev          # http://localhost:5173
```

For local development the Vite server reads the checked-in `site/api` snapshot, so the atlas and cards work without a backend or API key. The map uses OpenStreetMap tiles and needs a network connection for its basemap; the list and cards still work if tiles are unavailable.

To use the model paths, copy `.env.example` to `.env` and add your own Meta Model API key:

```sh
cp .env.example .env
# edit .env: META_API_KEY=...
set -a; . ./.env; set +a

# Permit reader
PYTHONPATH=core .venv/bin/python scripts/read_permit_with_muse.py \
  data/permits/tceq_amazon_ale60_pbr164110.pdf
```

`MODEL_OFF=1` disables every model call and uses the deterministic templates.

## Tests

```sh
# Python engine and API tests
PYTHONPATH=core .venv/bin/python -m pytest core/tests tests

# Frontend tests and type check
cd web
node --test scripts/*.test.mjs
npm run typecheck
```

## Data is public; no accounts are seeded

All application data is public. This repository ships **no user accounts, seed accounts, default passwords, admin bypasses or auth secrets**. The optional operator sign-in is an OIDC client integration; it is disabled unless you configure your own provider with `VITE_COGNITO_*`. The production provider configuration is not included. Signing in does not unlock private facility data.

## Live site and this repository

The live site **wattdocket.com** is deployed on AWS from a **separate private repository, `pankajarm/wattdocket`**. That repository contains the deployment configuration, collector schedules and account-specific infrastructure. **`wattdocket-core` is the open-source hackathon submission**, not the source of the live deployment, and it intentionally omits deployment-only files, account identifiers, ARNs, bucket names, operator emails and internal URLs.

The live site and this repository share the same application code base, but the public snapshot is curated: the history was squashed, deployment files were removed, and the remaining docs were scrubbed for publication.

## Development timeline

Development started **October 27, 2026**, after the hackathon kickoff. The public release history was **squashed into a single initial commit** for the open-source submission; later sync commits record subsequent public updates. Earlier development history is not part of this repository.

## License

Code is licensed under [Apache-2.0](LICENSE). See [NOTICE](NOTICE). Source datasets retain their publishers' terms and attribution requirements; see [DATA-PROVENANCE.md](DATA-PROVENANCE.md).
