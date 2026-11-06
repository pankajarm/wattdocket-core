# Security policy

## Reporting a vulnerability

Use GitHub's private [Report a vulnerability](https://github.com/pankajarm/wattdocket-core/security/advisories/new) form for suspected security issues. Do not put exploit details, credentials, personal data, or private infrastructure information in a public issue or pull request.

Include the affected commit, the component or route, reproducible steps, expected impact, and a minimal proof of concept where appropriate. Redact secrets and use systems and accounts you are authorized to test.

Ordinary data corrections, broken source links, and documentation problems belong in [public issues](https://github.com/pankajarm/wattdocket-core/issues/new/choose), unless they expose sensitive information or have a security impact.

## Supported code

Security fixes target the current `main` branch. There is no separate long-term-support branch or guaranteed response time. A published snapshot describes evidence at its recorded retrieval times; it is not a security support window.

## Running your own copy

- Keep API keys on the server. Never put secrets in `VITE_*` variables, which are included in browser builds.
- Keep `.env` files and local credentials out of version control. If a real key is exposed, revoke or rotate it at the provider; deleting the file does not remove the exposure.
- Do not expose the development server to untrusted networks. Configure HTTPS, request limits, allowed origins, and appropriate authentication before offering an optional model endpoint publicly.
- Treat upstream documents, source URLs, and model responses as untrusted input. Preserve hash verification, citation checks, and explicit fallback labels.
- Review dependency updates and the [hosting guidance](docs/DEPLOYMENT.md) before publishing a fork.

The repository contains no production credentials or seeded accounts. This statement is not a claim that the application has received an independent security audit.
