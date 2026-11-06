# Working with the public data snapshot

The repository bundles a pinned copy of a public WattDocket release so a fresh clone can browse evidence without connecting to the production data service. Installing frontend dependencies still requires access to the npm registry unless you have a suitable local cache. Basemap tiles, external fonts or media, and original source links can also require a network connection.

## What is pinned

`site/data/current.json` identifies one immutable release beneath `site/data/releases/`. It records the release's object paths, decoded content hashes, byte counts, build time, and coverage. Compressed files preserve the same decoded content as the public release. The local server exposes the paths expected by the application.

The manifest's build time, a source's retrieval time, and an observation's reporting period mean different things. A recent download can contain an older annual disclosure or a source that has not refreshed successfully. The app preserves those distinctions and its stale or unavailable labels.

Do not mix files from separate releases. Cards, source drawers, explanations, and the reporting explorer must read the same selected release. Do not edit a source value inside a published snapshot; correct the adapter or reviewed input and produce a new verified release.

## Verify a clone

From the repository root, using Python 3.11 or later:

```sh
python3 scripts/sync_public_data.py --verify-only
```

This checks the existing local snapshot without refreshing it or requesting new source data. A failed check means the snapshot should not be treated as verified. Preserve the error, restore the affected files from Git or obtain a complete validated release, and verify again.

## Refresh deliberately

To download the release currently published by WattDocket:

```sh
python3 scripts/sync_public_data.py
```

The synchronization tool pins the release pointer, downloads all objects belonging to that release, and validates content hashes before publishing the local snapshot. A failed download or mismatched hash must not replace the last complete snapshot. The live pointer may advance while the download runs; the downloaded release remains the one selected at the start.

Review the changed manifest and source status after refreshing. If you intend to contribute the snapshot, include the release ID and the verification result in your pull request. Do not commit credentials or private collector output.

Restart a running Vite development or preview server after a refresh. The server verifies and pins its data at startup so an in-progress session cannot silently mix releases. Rebuild `web/dist/` if you want the new release in a static build.

If Python reports a certificate verification error, use the virtual environment and development dependencies described in the README, which include the certificate bundle support used by the downloader, and run the command with that environment's Python. Do not disable HTTPS certificate verification.

## What this does not do

- It does not run the live site's collection schedule or fetch data directly from every upstream publisher.
- It does not update application code. Use Git to update the public repository, then reinstall dependencies when its lockfile changes.
- It does not create live facility measurements. Regional grid data, public disclosures, permit limits, and hypothetical scenarios retain their original scope.
- It does not copy production credentials, AWS infrastructure, authentication settings, or user accounts.
- It does not grant new redistribution rights for upstream material. Check [data provenance](../DATA-PROVENANCE.md) and publisher terms.

## Reproducing a result

Record the public repository commit, the data release ID, the record or reporting location, the measure and period, and any scenario inputs you changed. Include the source link or drawer citation when reporting a discrepancy. These details are more useful than a screenshot alone because they identify the evidence and calculation being compared.
