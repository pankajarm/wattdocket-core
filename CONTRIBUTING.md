# Contributing to WattDocket

WattDocket helps readers inspect public evidence about data centers. Contributions should make the evidence easier to trace and its limits easier to understand.

## Before starting

- Read the [README](README.md) for local setup and the [architecture](ARCHITECTURE.md) for the data flow.
- Search [existing issues](https://github.com/pankajarm/wattdocket-core/issues) before opening a new one. Discuss a large feature or source integration before building it.
- Follow the [code of conduct](CODE_OF_CONDUCT.md). Report vulnerabilities through the [security policy](SECURITY.md), not a public issue.

## Make a change

1. Fork the repository and create a branch from `main`.
2. Keep the change focused. Explain the user-visible problem and expected result.
3. Run the relevant checks in the README. Add a regression test for a fixed bug or a new data invariant; a documentation-only edit does not need application tests.
4. Open a pull request with the change, validation results, and any limitations. Include before-and-after screenshots for interface changes.

Use the committed dependency lockfile. Avoid unrelated formatting changes, generated build output, local environments, credentials, or production infrastructure configuration.

## Evidence and data changes

- Cite the original publisher and document or dataset URL. Include the reporting period, units, reporting boundary, retrieval date, and content hash where available.
- Preserve the distinction between a facility, a campus, an operator, a permit, and a regional grid. A shared name or address is not enough to allocate a reported total to an individual record.
- Keep missing data distinct from zero. Do not infer electricity demand from generator nameplate capacity, or measured annual emissions from a hypothetical load scenario.
- Preserve the source's wording for quoted permit conditions. Identify drafts, amendments, archived documents, and uncertainty about current effect.
- Add deterministic validation for calculations and parser changes. Model output is not source evidence or an independent calculation.
- Check source terms and attribution before adding data. Apache-2.0 covers project code, not every upstream dataset or document; see [data provenance](DATA-PROVENANCE.md).

Do not hand-edit a published snapshot to make a test pass. Update the relevant source adapter or reviewed input, rebuild through the documented process, and verify its manifest and hashes.

## Review expectations

Maintainers may request a smaller scope, clearer evidence, or additional checks. Contributions are reviewed as time permits; an issue or pull request does not imply a delivery commitment. By submitting a contribution, you agree that your original code is provided under the repository's [Apache-2.0 license](LICENSE), and that you have permission to contribute any included material.
