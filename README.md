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
- npm `>=10.9.0 <12`.
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

After the first public npm release, install it from the registry:

```bash
npm install --save-dev --save-exact @kcrong/agy-acp@0.1.0
```

Until npm publication, users with repository access can install directly from GitHub:

```bash
npm install --save-dev 'github:Kcrong/agy-acp#main'
```

The lockfile records the resolved commit. For a reproducible shared setup, replace `main` with a full commit SHA. Use existing GitHub authentication; never embed a token in the package URL. The Git dependency lifecycle compiles TypeScript before installation.

To work from a source checkout instead:

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

For a wrapper executable, repeat `--agy-arg` for static prefix arguments. These values come only from server startup configuration and are passed literally with `shell: false`:

```json
{
  "command": "npx",
  "args": [
    "--no-install",
    "agy-acp",
    "--agy-path",
    "/absolute/path/to/node",
    "--agy-arg",
    "/absolute/path/to/wrapper.mjs"
  ]
}
```

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
| `AGY_ACP_AGY_PATH` | `agy` | Bare command resolved once from absolute `PATH` entries, or an absolute executable path |
| `AGY_ACP_MAX_LINE_BYTES` | `4194304` | Maximum ACP/NDJSON line size |
| `AGY_ACP_MAX_STDERR_BYTES` | `65536` | Maximum retained diagnostic bytes per process |
| `AGY_ACP_MAX_SESSIONS` | `16` | Maximum active plus starting sessions |
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
- The `agy` executable is resolved to an absolute realpath before any client-provided working directory is accepted; relative path values are rejected.
- Cancellation targets the detached POSIX process group or Windows process tree, waits for the tree-signaling command, and applies a bounded post-kill cleanup deadline.
- Conversation IDs, token usage, stderr contents, and prompt text are excluded from default diagnostics.
- The adapter adds no telemetry or network service. Network requests are made only by `agy` itself. Upstream `agy` may collect interaction data under its own terms and provides an opt-out setting; review the [Antigravity CLI data-use notice](https://github.com/google-antigravity/antigravity-cli#terms-of-service--data-use).

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Development

```bash
npm ci --ignore-scripts
npm run check
```

`npm run check` runs lint, strict type checking, unit tests, credential-free fake-process E2E tests, and the build.

`npm run test:package` builds a real tarball, installs it into a new temporary consumer project, verifies package imports and the installed CLI, then removes its scratch directory.

To verify the GitHub dependency path against an accessible revision:

```bash
npm run test:git-install -- 'github:Kcrong/agy-acp#main'
```

The smoke test installs into session scratch, verifies the package import and CLI, and does not print Git credentials or resolved authentication data.

See [Technology stack decision](docs/technology-stack.md) for why the adapter remains on Node.js and TypeScript and when a native rewrite should be reconsidered.

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
