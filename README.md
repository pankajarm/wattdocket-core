# WattDocket

**Public records on US data centers, cited to the source.** Explore permits, reported electricity and water data, and regional grid context. Follow each claim to its evidence and see where the public record is incomplete.

[Live website](https://wattdocket.com/) · [Local quickstart](#run-locally) · [Architecture](ARCHITECTURE.md) · [Data provenance](DATA-PROVENANCE.md) · [Contributing](CONTRIBUTING.md)

Originally built for the **Meta Global AI Developer Hackathon, Indie track**. This public repository includes the application and data-processing code, a pinned public data release, and tools to run and verify a local copy. AWS account configuration and production deployment automation are excluded.

## Watch the demo

[![Watch the WattDocket demo — 2 minutes 52 seconds](https://wattdocket.com/media/wattdocket-demo-31ba55c1-poster-v2.jpg)](https://wattdocket.com/media/wattdocket-demo-31ba55c1.mp4)

**[Watch the 2:52 demo](https://wattdocket.com/media/wattdocket-demo-31ba55c1.mp4)** · [Watch with captions and chapter shortcuts](https://wattdocket.com/)

See reported data, quoted permit conditions, a live Meta Muse Spark explanation, and the historical grid calculator.

## Run locally

Use **Node.js 22 or a newer supported LTS release**, npm, and Git. Python, an AWS account, a sign-in, and a model API key are not required to browse the bundled data.

```sh
git clone https://github.com/pankajarm/wattdocket-core.git
cd wattdocket-core/web
npm ci
npm run dev
```

Open **http://localhost:5173**. The committed data snapshot is served locally; normal browsing does not fetch the live website's data. Map basemaps, externally hosted fonts or media, and original publisher links may still need internet access. Missing network access to an external service does not turn missing data into a measured zero.

To check a production build locally:

```sh
# From web/
npm run build
npm run preview
```

Open the URL printed by Vite. No `.env` file is needed for the default experience. See [support](SUPPORT.md) if installation or a page fails.

## What you can explore

- **Reported data.** Search operator and municipal disclosures by measure, reporting period, publisher, and scope. A campus or operator total stays within its stated reporting boundary.
- **Facility records.** Search by name, address, or EPA ECHO identifier. Inspect source records, program status, location confidence, and reviewed identity links. A record is not proof of a complete physical-site footprint.
- **Permits and citations.** Inspect quoted conditions, source documents, amendments, and the evidence available about current effect. Name-only matches remain candidates.
- **Regional grid context.** Explore official annual eGRID factors, a separately labeled independent comparison, EIA-930 reported hourly data, and ERCOT fuel generation. Regional data is not a facility meter.
- **Historical scenarios.** Edit a reference load and utilization assumptions to explore a calculation. The result is a hypothetical scenario, not measured facility consumption or emissions.
- **Plain-English explanations.** Read a release-pinned deterministic summary by default. An optional server-side Meta Muse Spark service can explain the verified evidence with checked citations and qualifications.
- **Evidence exports and learning guides.** Review source drawers, methods, timestamps, and reporting boundaries; use the guided explainers to understand the measures.

WattDocket does not infer a facility's actual electricity demand, water use, or annual emissions when the public record is silent. Missing, withheld, stale, and unavailable values retain their labels. A stale source can still document a historical reporting period; it is not evidence of present-day operation.

## Data snapshot and live-site parity

`site/data/current.json` pins the bundled release and describes its objects, content hashes, build time, and coverage. The release's source status and retrieval dates remain visible in the app. This is a reproducible snapshot, not a promise that your clone will update whenever the live site's collectors run.

See the [source synchronization record](docs/SOURCE-SYNC.md) for the audited production revision, scope, and intentional local adaptations.

With **Python 3.11+**, verify the bundled data without a network request:

```sh
# From the repository root
python3 scripts/sync_public_data.py --verify-only
```

To deliberately download the current public release:

```sh
python3 scripts/sync_public_data.py
```

The downloader verifies every object's hash before publishing the local snapshot. Read [Data snapshots](docs/DATA-SNAPSHOT.md) for the workflow and [Data provenance](DATA-PROVENANCE.md) for sources, attribution, and limitations. Refreshing a data snapshot does not synchronize application code; update the repository separately.

## Meta Muse Spark

The optional explanation path uses **Meta Muse Spark through the Meta Model API**. Source records and deterministic calculations establish the numbers. The model explains trusted facts; its response is checked before presentation.

The current explanation contract binds each request to an immutable release and a supported subject: a facility, disclosure location, power site, or permit document. The server loads the matching verified fact registry and checks cited facts, numerals, units, and required qualifications. These checks do not prove every semantic claim. Failed validation or a model failure produces an explicitly labeled trusted fallback; it must not be presented as a model answer.

- `core/wattdocket/production/explain_registry.py` builds the trusted subject facts.
- `api/lambda_explain/` contains the guarded explanation handler and validators.
- `web/src/components/ExplainPanel.tsx` presents the response and validation state.
- `scripts/read_permit_with_muse.py` is a separate permit-reading tool; quoted obligations must match the document text.

Local browsing works without this service. To make new model requests, use your own Meta Model API credentials on the server and follow the optional local-service instructions in [the explain documentation](api/lambda_explain/README.md). Never put a secret in a `VITE_*` variable or commit it to Git. `MODEL_OFF=1` disables model calls in the server-side tools that support the model path.

## Development and checks

Frontend checks:

```sh
cd web
npm ci
node --test scripts/*.test.mjs
npm run typecheck
npm run build
```

For the Python engine, parsers, and backend tests, use **Python 3.11+**:

```sh
# From the repository root
python3 -m venv .venv
.venv/bin/python -m pip install -e 'core[dev,api]'
MODEL_OFF=1 PYTHONPATH=core .venv/bin/python -m pytest core/tests tests
```

On Windows, create the environment with `py -m venv .venv` and use `.venv\Scripts\python.exe` in place of `.venv/bin/python`. In PowerShell, set `$env:MODEL_OFF="1"` and `$env:PYTHONPATH="core"` before the test command.

## Repository layout

| Path | Purpose |
| --- | --- |
| `web/` | React and TypeScript app, local server configuration, and frontend tests |
| `core/wattdocket/` | Deterministic calculations, evidence models, source adapters, and release builders |
| `core/tests/`, `tests/` | Engine, parser, backend, and evidence validation tests |
| `api/` | Explanation service code and development API tooling |
| `site/data/` | Pinned, hash-verified public data snapshot |
| `data/` | Reviewed inputs, source extracts, and public document fixtures |
| `scripts/` | Data synchronization, validation, and research utilities |
| `docs/` | Source methodology, local data workflow, and hosting guidance |

## Contributing and support

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change. Use [issues](https://github.com/pankajarm/wattdocket-core/issues/new/choose) for bugs, source corrections, and feature proposals, and the [private security reporting process](SECURITY.md) for vulnerabilities. Project participation follows the [code of conduct](CODE_OF_CONDUCT.md).

For your own hosted instance, see [Deployment guidance](docs/DEPLOYMENT.md). The public repository does not provision AWS, manage the live site's collectors, or supply an identity provider. Optional authentication requires your own configuration and does not verify facility ownership or unlock private facility measurements.

## Project history and license

Development began on **October 27, 2026**. The initial public submission was a curated, squashed snapshot; subsequent commits record public updates. The project continues beyond the original hackathon submission.

Code is licensed under [Apache-2.0](LICENSE); see [NOTICE](NOTICE). Source datasets and documents retain their publishers' terms and attribution requirements, described in [DATA-PROVENANCE.md](DATA-PROVENANCE.md).
