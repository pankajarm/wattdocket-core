# Web application

The React and TypeScript app reads the verified public data bundled with this repository. It includes reported data, learning pages, public records, permit evidence, regional grid views, the historical grid calculator and the demo video.

## Start locally

Use Node.js 22.12+ or 24+. From this directory:

```sh
npm ci
npm run dev
```

Open [localhost:5173](http://localhost:5173). No AWS account, user account or model key is required. The data is a fixed snapshot of the public site; it is not a live feed. The base map, web fonts and external source links need network access, while bundled records and demo media are local.

Example routes:

- [Reported data](http://localhost:5173/#/reported)
- [TX2 public record](http://localhost:5173/#/public/echo%3A110072155814)
- [Source status](http://localhost:5173/#/data)

## Build and preview

```sh
npm run build
npm run preview
```

Preview serves [localhost:4173](http://localhost:4173). The build output is `dist/`. It contains the selected data release as ordinary JSON under `data/` and the demo assets under `media/`, as well as the application bundle. A static host can serve the directory without Python or a separate data API. See [hosting guidance](../docs/DEPLOYMENT.md) before publishing a copy.

## How the snapshot is served

`scripts/local-snapshot.ts` reads `../site/data/current.json`, checks the matching immutable manifest and verifies each object's uncompressed size and SHA-256. It then serves the selected snapshot in development and preview, and emits it into the production build. A missing or corrupted snapshot stops startup instead of silently using the live service.

From the repository root:

```sh
# Offline integrity check
python3 scripts/sync_public_data.py --verify-only

# Explicitly download and verify a newer public release
python3 scripts/sync_public_data.py
```

Review changed objects before committing an update. Restart Vite after a refresh; an already-running server keeps its selected release. See [snapshot maintenance](../docs/DATA-SNAPSHOT.md).

## Optional local Muse Spark explanations

Model calls are disabled unless you configure an endpoint. To exercise the local handler with labeled deterministic fallbacks, run this from the repository root:

```sh
python3 scripts/serve_local_explain.py
```

Create `web/.env.local` with:

```dotenv
VITE_EXPLAIN_URL=http://127.0.0.1:8787/explain
```

Restart Vite after changing environment variables. The adapter serves the registry from the same local snapshot and accepts the default development and preview origins. It does not need AWS credentials.

For actual Muse Spark calls, supply your own `META_API_KEY` to the server environment and explicitly opt in:

```sh
MODEL_OFF=0 python3 scripts/serve_local_explain.py --enable-model
```

The key stays server-side. Never put it in a `VITE_*` variable. Calls consume your Meta API quota; the adapter defaults to ten attempts per UTC day per process. Its counter resets on restart. Keep this development adapter on loopback; it is not intended for a public server.

`VITE_API_BASE` is a legacy integration override that bypasses release-backed reads; leave it unset for the supported bundled-snapshot workflow. Optional `VITE_COGNITO_*` settings must refer to your own identity provider. No production provider is configured in this repository.

## Checks

```sh
npm run typecheck
npm test
npm run build
npm audit --audit-level=high
```

Frontend tests use fixtures and mocked responses. They do not call the live explanation service. CI runs these checks on Node 22 and 24. Python evidence and handler tests are described in the [contribution guide](../CONTRIBUTING.md).

For the local browser smoke test, install Chromium once and build first:

```sh
npx playwright install chromium
npm run build
npm run test:browser
```

The browser check starts its own local server and blocks external requests. It checks that the clone can use its bundled records and media without the live website or a model key.
