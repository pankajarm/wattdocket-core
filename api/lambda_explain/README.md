# Release-pinned explanations

The explanation handler accepts a selected data release and a supported subject. Its trusted inputs come from that release's verified fact registry. It uses Meta Muse Spark through the Meta Model API, checks the response, and returns a labeled source-evidence fallback when a model answer cannot be accepted.

## Run locally without AWS

From the repository root, with Python 3.11 or later:

```sh
python3 scripts/serve_local_explain.py
```

The adapter uses Python's standard library and binds to **127.0.0.1:8787**. It serves the same release-pinned facts and validation logic as the production handler, using the bundled local snapshot for storage. It makes **no model calls by default**, even if an API key is present in your environment.

Add this public endpoint URL to `web/.env.local`:

```dotenv
VITE_EXPLAIN_URL=http://127.0.0.1:8787/explain
```

Restart Vite after changing its environment. Its default development origin (`localhost:5173`) and preview origin (`localhost:4173`), and their `127.0.0.1` equivalents, are allowed. For a different local port, add it explicitly:

```sh
python3 scripts/serve_local_explain.py --allow-origin http://localhost:5174
```

The adapter is for loopback development. It restricts Host and Origin values and is not a publicly hosted service.

## Opt in to your own Meta API calls

Set `META_API_KEY` in the server's environment using your own key. Do not put it in `web/.env.local`, a `VITE_*` variable, a screenshot, or Git. If you keep it in a root `.env` file, that file is ignored by Git but is not loaded automatically by this adapter; load the variables into the process environment yourself.

With the key set, explicitly enable calls:

```sh
MODEL_OFF=0 python3 scripts/serve_local_explain.py --enable-model
```

In PowerShell, set `$env:MODEL_OFF="0"` before running the command. `MODEL_OFF=1` blocks model opt-in. Without `--enable-model`, the adapter always returns the labeled source-evidence summary.

The local limit defaults to **10 attempted calls per UTC day per process**, configurable with `--daily-limit` from 1 to 100. **Restarting the process resets this counter.** This is a development guard, not a durable spending cap or a replacement for provider-side limits. Calls may incur charges and require account access to the configured model. Failed or uncertain admitted attempts are not refunded or automatically retried.

Use `python3 scripts/serve_local_explain.py --help` for `--data-dir`, `--port`, and the other available options. A different data directory must contain a complete, validated snapshot using the same manifest layout.

## Request and response contract

The request body is:

```json
{
  "release_id": "<release_id from site/data/current.json>",
  "subject": {
    "kind": "facility",
    "id": "echo:110072155814"
  }
}
```

Supported kinds are `facility`, `disclosure_location`, `power_site`, and `permit_document`. A permit-document explanation describes that document; it does not establish an inventory-to-site match. Subject IDs must exist in the selected registry. Client facts, URLs, prompts, and fallback wording are ignored.

The handler verifies the selected manifest and registry bytes. A missing or invalid release never falls back to a different release or the former packaged catalogue. Legacy requests need a valid release ID; unpinned old clients receive `CLIENT_UPDATE_REQUIRED` before a model call.

The model receives complete trusted facts within a bounded prompt and returns paragraphs with fact IDs. Checks cover cited facts, signed numerals, numeric-unit pairs, required qualifications, and known scope or period contradictions. Allowances come from the cited fact text, not arbitrary metadata or identifiers. The response includes cited facts and source links.

These checks do not prove every semantic statement. Accepted model prose identifies the validation level as `numerals_citations_and_qualifiers`. Invalid prose, an upstream failure, deadline pressure, or quota denial returns the selected release's trusted summary with a reason and `trusted_fallback` validation. A registry failure returns an unavailable response so the client can retain its own pinned summary.

## Tests and hosting

The normal Python test suite includes the explanation handler, response validation, registry loading, quota behavior, and local adapter. See the [README](../../README.md#development-and-checks).

The AWS-specific storage and quota adapters remain part of the handler implementation, but local development replaces them without AWS credentials or service calls. The old `api/wattdocket_api/` service is legacy development code and is not the recommended explanation server. Follow [hosting guidance](../../docs/DEPLOYMENT.md) if you intend to operate a separate public endpoint; production provisioning is outside this repository's scope.
