# Security Policy

## Supported versions

Until the first stable release, only the latest `0.x` release receives security fixes.

## Reporting a vulnerability

Use GitHub's private security advisory flow for this repository:

1. Open the repository's **Security** tab.
2. Select **Advisories** and **Report a vulnerability**.
3. Include affected versions, impact, and minimal reproduction steps.

Do not open a public issue for an unpatched vulnerability. Never include credentials, access tokens, Antigravity authentication files, private prompts, conversation IDs, or real user data in a report.

If private reporting is unavailable because the repository has not yet been made public, contact the repository owner through an already verified private channel and reference only the issue summary. Do not send secrets.

## Security model

`agy-acp` launches the locally installed `agy` executable and inherits the caller environment so Antigravity can use its existing authentication. The adapter:

- does not read credential files;
- does not print environment values, prompt contents, conversation IDs, usage, or retained stderr;
- does not invoke a shell for `agy`;
- does not enable `--dangerously-skip-permissions`;
- bounds protocol lines, diagnostics, initialization, prompts, cancellation, and shutdown;
- rejects unsupported ACP content and client-provided MCP configuration.

Issues that bypass these boundaries, leak data to stdout/stderr, leave child processes running, or permit argument injection are security-sensitive.
