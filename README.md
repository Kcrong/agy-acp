# @kcrong/agy-acp

`agy-acp` exposes the Google Antigravity CLI (`agy`) as a local [Agent Client Protocol (ACP) v1](https://agentclientprotocol.com/) agent over JSON-RPC/stdio.

> This is an independent community adapter. It is not an official Google product.

## What it does

```text
ACP client/editor
      │ JSON-RPC over stdio
      ▼
   agy-acp
      │ stream-json NDJSON
      ▼
     agy
```

- Starts one isolated `agy` process per ACP session.
- Streams Antigravity text deltas as ACP `agent_message_chunk` updates.
- Supports ACP session creation, loading, prompting, cancellation, and closing.
- Cleans up child processes on cancellation, timeout, close, and client disconnect.
- Never reads Antigravity credential files. Authentication remains owned by `agy`.
- Writes only ACP protocol messages to stdout. Diagnostics use stderr.

## Requirements

- Node.js `>=22.13.0` (Node.js 22 and 24 are tested targets).
- `agy >=1.2.14` installed and authenticated in the same user environment.
- An ACP v1-compatible client that can launch a local stdio agent.

Check Antigravity before starting:

```bash
agy --version
agy models
```

The second command verifies server access without exposing token values.

## Installation

The npm package name is scoped because the unscoped `agy-acp` name belongs to an unrelated project.

After the first public release, install it in your workspace:

```bash
npm install --save-dev --save-exact @kcrong/agy-acp@0.1.0
```

Until publication, build this repository locally:

```bash
npm ci --ignore-scripts
npm run build
```

No global package installation or `sudo` is required.

## ACP client configuration

A generic workspace-local configuration is:

```json
{
  "command": "npx",
  "args": ["--no-install", "agy-acp"]
}
```

To select a specific Antigravity executable:

```json
{
  "command": "npx",
  "args": [
    "--no-install",
    "agy-acp",
    "--agy-path",
    "/absolute/path/to/agy"
  ]
}
```

Client configuration keys differ between editors. Map the `command` and `args` values above to your client's local stdio-agent settings.

For a source checkout, use `node` with the absolute path to `dist/cli.js` after `npm run build`.

## Supported ACP surface

Baseline support:

- `initialize`
- `session/new`
- `session/prompt`
- `session/cancel`
- `session/update`

Implemented but not advertised until their complete compatibility matrix is verified:

- `session/load`
- `session/close`
- Additional workspace directories

Prompt input supports ACP Text and ResourceLink blocks. Image, audio, embedded resources, client-provided MCP servers, document notifications, provider management, session modes, and NES are not advertised. Unsupported input is rejected with a protocol error rather than silently ignored.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `AGY_ACP_AGY_PATH` | `agy` | Antigravity executable name or path |
| `AGY_ACP_MAX_LINE_BYTES` | `4194304` | Maximum ACP/NDJSON line size |
| `AGY_ACP_MAX_STDERR_BYTES` | `65536` | Maximum retained diagnostic bytes per process |
| `AGY_ACP_INIT_TIMEOUT_MS` | `15000` | Child initialization timeout |
| `AGY_ACP_PROMPT_TIMEOUT_MS` | `1800000` | Prompt timeout |
| `AGY_ACP_CANCEL_GRACE_MS` | `5000` | Grace period before hard termination |
| `AGY_ACP_HARD_KILL_GRACE_MS` | `2000` | Final cleanup wait after hard termination |

Every numeric value must be a positive safe integer. Invalid values fail closed without echoing their contents.

The command-line `--agy-path` option takes precedence over `AGY_ACP_AGY_PATH`.

## Security and privacy

- `agy-acp` inherits the caller environment so `agy` can use its existing login. Environment variables are never enumerated or logged.
- The adapter does not enable `--dangerously-skip-permissions`.
- User-controlled paths and identifiers are passed as literal argv entries with `shell: false`.
- Conversation IDs, token usage, stderr contents, and prompt text are excluded from default diagnostics.
- No telemetry or network service is added by the adapter. Network requests are made only by `agy` itself.

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Development

```bash
npm ci --ignore-scripts
npm run check
```

`npm run check` runs lint, strict type checking, 50 unit tests, credential-free fake-process E2E tests, and the build.

The real authenticated smoke test is opt-in:

```bash
RUN_REAL_AGY_E2E=1 \
AGY_E2E_AGY_PATH=/absolute/path/to/agy \
npm run test:e2e -- tests/e2e/real-agy.e2e.test.ts
```

The test compares only a response hash and never prints response text, conversation IDs, usage, credentials, or environment values.

## Troubleshooting

### `Failed to start agy process`

Confirm `agy` is on `PATH`, or pass `--agy-path` with an absolute executable path.

### `agy process initialization timed out`

Run `agy models` in the same terminal account. Complete login there if requested, then retry the ACP client.

### Prompt returns an internal error

Run the local fake-process suite first:

```bash
npm run check
```

If local checks pass, confirm `agy 1.2.14` or newer still supports `--input-format=stream-json --output-format=stream-json`.

### Client-provided MCP servers are rejected

This release does not mutate global Antigravity MCP configuration. Configure MCP directly in `agy`, or wait for a process-scoped Antigravity MCP interface.

## Versioning and release safety

The project follows Semantic Versioning. While the package is `0.x`, ACP behavior may evolve between minor releases. The repository remains `private: true` in `package.json` until an explicit release decision removes that guard, verifies package contents, and publishes the scoped package.

## License

Apache License 2.0. See [LICENSE](LICENSE).
