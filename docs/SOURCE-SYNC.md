# Source and live-site synchronization

This public repository contains the application code and public data needed to
run WattDocket locally. AWS account configuration, deployment automation,
collector schedules, credentials and operational logs are intentionally excluded.

## Audited baseline

The November 6, 2026 synchronization compared the production source at revision
`1f69c7855412f505771a0073d9407582426a2a52` with the public checkout. The live
frontend bytes matched the staged production artifact with SHA-256
`772727e18c320459fd2e04ebabcc9fd45182620c4b76f2878e7b8a035e997254`.

`source-sync.json` records SHA-256 values for every tracked application module,
schema and reviewed input in the audited source scope. Public runtime additions
are listed separately. Run:

```sh
python3 scripts/verify_source_sync.py
# Maintainers with the production source can also compare it directly:
python3 scripts/verify_source_sync.py --source /path/to/production-checkout
```

The data baseline is release `20261106T124418Z-a50ee40cfe2c`, built on November 6,
2026 at 12:44:18 UTC. All 130 manifest objects are included, with their original
uncompressed hashes and sizes. Compressed storage occupies approximately 10 MB;
decoded JSON totals 126,409,758 bytes. No public object in that release was
omitted. Historical source fixtures under `data/` and `site/api/` remain separate
from this selected snapshot. The default app reads the selected snapshot.

## Intentional public adaptations

- The browser has no production model endpoint configured. Calls require an
  explicit local or independently hosted endpoint, and an unconfigured call
  returns a labeled fallback without making a request.
- Vite serves and builds the verified local snapshot instead of proxying live
  data. The published demo, poster and captions are included as local assets.
- The development toolchain is patched independently of the live bundle. This
  release uses Vite 7.3.7, with compatible React tooling and explicit test
  dependencies. This is not a byte-identical copy of the production JavaScript
  bundle; the application source parity is recorded above.
- The optional localhost explanation adapter replaces AWS storage and durable
  quota infrastructure with verified filesystem reads and a bounded in-memory
  quota. It reuses the production prompt and response validators. Paid calls
  require explicit opt-in and the developer's own credential.
- The obsolete development API's model route is retired with HTTP 410. It no
  longer accepts caller-provided facts for model calls or reads a credential
  store implicitly. Its optional deterministic endpoints remain available;
  the supported explanation adapter is documented separately.
- Public tests omit AWS edge/deployment configuration and private planning-doc
  consistency checks. Executable calculation and evidence invariants remain.
  Retired `explain.json` code and tests were removed to match the current
  release-pinned subject registry and its replacement tests.

## Maintaining parity

1. Identify the source revision actually serving the live app. Compare artifact
   hashes or its release identity; a source commit alone is not proof of deployment.
2. Review application, source-adapter, schema, parser and test changes. Carry
   forward the intentional public adaptations and check upstream deletions too.
   Never copy a deployment checkout wholesale into the public repository.
3. Run `python3 scripts/sync_public_data.py` to pin and verify a current public
   release. Restart the local server or rebuild to select it. Remove superseded
   bundled releases only after verifying the replacement; Git retains history.
4. Update the source hash record after reviewing the changes. Run the Python,
   frontend, browser and snapshot checks documented in the README and `web/README.md`.
5. Review the diff for credentials, private infrastructure and generated files;
   run a secret scanner. Publish through a pull request and require its checks
   to pass before merging.

The live collectors continue independently. A clone is reproducible at its
recorded release, not automatically current forever. Code updates use Git;
explicit snapshot refreshes update data. Hosting guidance is in
[DEPLOYMENT.md](DEPLOYMENT.md); this repository does not provision AWS resources.
