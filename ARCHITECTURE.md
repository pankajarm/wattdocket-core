# Architecture

WattDocket has one design rule: **deterministic code owns every number; the model only reads and explains.** The public site can render entirely without a model call, and the model path fails closed to a template.

This document describes the open-source application. The live deployment's infrastructure configuration lives in the separate private repository `pankajarm/wattdocket` and is intentionally not part of this repo.

## 1. Runtime shape

```
Public sources ──► deterministic collectors ──► immutable data snapshot
                                                    │
                                                    ▼
                                  site/api + site/data (checked in)
                                                    │
                          ┌─────────────────────────┴──────────────────────┐
                          ▼                                                ▼
                 React + Vite SPA                                  FastAPI (local dev)
                 atlas · cards · drawers · grid · operator         api/wattdocket_api
                          │
                          │ optional explain call
                          ▼
                 explain handler (api/lambda_explain)
                          │ server-side only, guarded
                          ▼
                 Meta Model API · Muse Spark
```

The browser reads precomputed JSON. It does not compute carbon, water or permit facts at request time, and it never receives a model key.

## 2. Components

| Layer | Code | Responsibility |
| --- | --- | --- |
| Calculation engine | `core/wattdocket/` | Units, time zones, grid intensity, water states, estimates, citations and permit verification |
| Release builder | `core/wattdocket/production/` | Source adapters, identity checks, evidence validation, immutable card/explain payloads |
| Local API | `api/wattdocket_api/` | Development and integration surface over the artifacts |
| Explain boundary | `api/lambda_explain/`, `api/wattdocket_api/explain.py` | Sends only server-owned facts to Muse Spark and validates the reply |
| Web app | `web/src/` | React + TypeScript atlas, public cards, source drawers, grid views and operator bench |
| Static data | `site/api/`, `site/data/`, `data/` | Checked-in snapshot used for local development and tests |
| Permit reader | `scripts/read_permit_with_muse.py`, `core/wattdocket/permits.py` | Model proposes page/line/quote; Python verifies the quote |

## 3. The model boundary

1. **Explain.** The handler receives a facility id, looks up the server-owned fact set for that card, and asks Muse Spark for two plain sentences. `core/wattdocket/prose.py` (via `assert_bound`) rejects any numeral that is not in the fact set. A rejection, timeout, missing key or `MODEL_OFF=1` returns the deterministic template with `source: "template"`.
2. **Permit reading.** The model reads a permit and returns obligations with page, line and a verbatim quote. `core/wattdocket/permits.py` checks each quote against the document text and drops anything it cannot find. Only verified pointers ship in `artifacts/permit_read.json`.
3. **No client text.** The browser sends only the facility id and the pinned release id. Client-supplied facts, template strings and arbitrary prompts never reach the model.
4. **No model arithmetic.** The model is never asked to add, multiply, convert units or infer a missing value.

## 4. Data flow

The build reads public source records, validates identity and evidence, and writes an immutable release. Each release contains:

- the facility index and public cards;
- source drawers keyed by claim;
- operator payloads and grid series;
- per-card explain facts and deterministic summaries;
- a manifest that pins every path and hash.

The browser reads `/data/current.json` once per session and pins that release. A failed record read never falls back to an older or fabricated payload. The checked-in `site/api` and `site/data` snapshots let the app and tests run without the live deployment.

## 5. Local development

```sh
python3 -m venv .venv
.venv/bin/pip install -e "core[dev]"
cd web && npm ci && npm run dev
```

`web/vite.config.ts` serves the checked-in `site/api` snapshot at `/api/v1/*` during development. Set `VITE_API_BASE` to point at the FastAPI service instead:

```sh
PYTHONPATH=core:api .venv/bin/python -m uvicorn wattdocket_api.main:app --port 8090
# another terminal
cd web && VITE_API_BASE=http://127.0.0.1:8090 npm run dev
```

## 6. Deployment boundary

The open-source repo contains no deployment manifests, cloud account identifiers, bucket names, ARNs, operator emails or internal URLs. The live site is built and deployed from the separate private repository; see the README's "Live site and this repository" section.
