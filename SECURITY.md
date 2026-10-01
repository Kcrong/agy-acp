# Security Policy

## Supported scope

Security fixes target the latest `main` revision and the latest published release, when one exists. Supported runtimes are Python 3.13 and 3.14 on Linux and macOS. Windows is outside the current support scope.

## Data boundary

The adapter forwards supported ACP prompt content and resource references to the user's installed, authenticated `agy` process. It adds no telemetry endpoint, independent network listener, or payload store. Processing performed by `agy` is governed by that CLI, its account, and its configured upstream services.

## Reporting a vulnerability

Do not publish vulnerability details, credentials, prompts, responses, conversation identifiers, environment data, or private endpoints in an issue. Open a minimal issue titled `Security contact request` with no technical details or sensitive values so a private reporting channel can be arranged.

Reports should later include the affected revision, impact, minimal reproduction, and any known mitigation. Repository-facing discussion and test fixtures must remain sanitized.
