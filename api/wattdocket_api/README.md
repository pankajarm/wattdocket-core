# Legacy deterministic API

You do **not** need this service to run WattDocket locally. The website reads the bundled, verified data release through Vite. Use the [main quickstart](../../README.md) for the complete current website.

This older development service exposes ledger/fixture records, source drawers and the deterministic estimator. Its data format predates the current release manifests. It does not provide the full current website API and is not a replacement for the bundled snapshot.

## Run it locally

From the repository root, in your Python virtual environment:

```sh
python -m pip install -e "core[api]"
PYTHONPATH=core:api python -m uvicorn wattdocket_api.main:app --host 127.0.0.1 --port 8080
```

PowerShell:

```powershell
python -m pip install -e "core[api]"
$env:PYTHONPATH = "core;api"
python -m uvicorn wattdocket_api.main:app --host 127.0.0.1 --port 8080
```

Open [the API documentation](http://127.0.0.1:8080/docs) or [health status](http://127.0.0.1:8080/healthz). The service reads `ledger.sqlite` if present, otherwise `artifacts/fixtures.json`; without either it reports an empty ledger. `WATTDOCKET_LEDGER` and `WATTDOCKET_ARTIFACTS` override these paths. All paths are relative to the working directory unless absolute.

Do not configure the current website to use this service as a replacement for its snapshot. Use these routes only for the older ledger/fixture development workflow.

## Explanations

`POST /v1/explain` returns **410 Gone** with `LEGACY_EXPLAIN_RETIRED`. It never forwards caller-provided facts, returns caller-written text as trusted evidence, looks up credentials, or calls a model. `wattdocket_api.explain.compose()` also raises an explicit retirement error.

For current explanations, use [the local explanation server](../lambda_explain/README.md#run-locally-without-aws). It reads the pinned snapshot, reuses the production validation, and supports explicit opt-in to your own Meta Model API key. The legacy service has no model-enabled mode.
