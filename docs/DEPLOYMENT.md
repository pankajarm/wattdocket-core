# Hosting your own WattDocket instance

The public repository includes application code, data-processing code, public data, and local development tools. It does not include the live site's AWS account configuration, infrastructure definitions, credentials, or automated deployment jobs. This guide describes the pieces a separate hosting setup needs.

## Start with a local build

Complete the [README quickstart](../README.md) and verify the data snapshot before hosting. Build the frontend with `npm run build` from `web/`, then check it with `npm run preview`. The resulting `web/dist/` is a static browser application.

The build includes the selected data release as ordinary JSON files and the bundled demo media. Upload the complete output directory, not just its entry HTML and JavaScript. The compressed source snapshot in the repository is decoded during the build; a static host does not need a custom gzip-file mapping for this output.

Choose a pinned data release for the instance. Keep its manifest, relative object paths, and hashes together; never assemble a release from individual files taken from different refreshes. Do not point a fork at the live production explain endpoint or authentication provider.

## Static website and data

A static host can serve the application and public JSON. S3 with CloudFront is one option; a conventional web server or another static hosting service can also work. The browser should receive the frontend and `/data/` on the same origin unless you deliberately configure and test another arrangement.

Your host needs to:

1. Serve the frontend entry point and generated assets, including deep-link reloads. WattDocket uses hash routes, so the fragment stays in the browser.
2. Serve the selected data release beneath `/data/releases/<release-id>/`, plus the release pointer at `/data/current.json`.
3. Return JSON with `application/json`, video with `video/mp4`, and captions with `text/vtt`. A missing data object must return a real error, not an HTML application shell with HTTP 200.
4. Cache immutable release objects by their immutable paths. Give the mutable current-release pointer a short cache lifetime and revalidate it.
5. Use HTTPS, disable directory listing, and avoid publishing local environment files, source archives containing secrets, test reports containing private details, or deployment credentials.

Keep the original source links and attribution. Public accessibility does not give every upstream document an Apache-2.0 license; review [data provenance](../DATA-PROVENANCE.md) and the publisher's terms before redistributing or republishing data.

The local copy does not run a collector schedule. If you operate a collector, choose appropriate refresh intervals, retain retrieval and failure metadata, respect upstream access rules, and publish a validated release atomically. An upstream outage must not replace missing evidence with an invented value.

## Optional model service

Browsing public evidence does not require a model or API key. To offer new Meta Muse Spark explanations, operate a server-side endpoint with your own Meta Model API credentials and the same selected release as the browser. Follow the explain handler's documented request format and validation contract.

Keep API keys out of the frontend. Every `VITE_*` value can be read by a visitor. Set request-size limits, timeouts, origin policy, admission limits, and a spending budget before exposing a paid model endpoint. Preserve citation, numeral, qualification, and release checks, and label a deterministic fallback explicitly. A service failure must not silently substitute facts from a different release.

An AWS implementation may use a private data bucket, a CDN for public static assets, and a separate serverless API with narrowly scoped permissions. This repository does not provision those services or configure billing, DNS, certificates, identity providers, or monitoring for you.

## Optional authentication

The default local experience does not require an account. A hosted operator sign-in needs your own OIDC provider, registered callback and logout URLs, consent settings, and publicly documented privacy practices. Do not copy the live site's client or tenant configuration. Authentication does not establish facility ownership or turn public records into private meter data.

## Before making the instance public

Verify logged-out access, deep links, the pinned release's hashes, source drawers, captions and video where included, mobile layouts, and failure states. If you enable a model service or authentication, test those flows independently. Record the source revision and data release you deployed, retain the previous working release, and document how to restore it.
