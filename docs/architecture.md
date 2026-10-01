# Python architecture decision

**Status:** Accepted on 2026-10-01 with the stdio MCP handoff as a release gate

## Decision

Build `agy-acp` as a Python package using the official stable ACP Python SDK schema models and the standard-library asynchronous process APIs. A project-owned strict JSON-RPC boundary validates and redacts input before SDK model construction. The package supports Python 3.13 and 3.14 on Linux and macOS.

The implementation is a protocol and subprocess adapter. It does not implement a model provider, credential store, network service, or terminal UI.

## Runtime stack

| Component | Selected version or policy | Reason |
| --- | --- | --- |
| Python | `>=3.13,<3.15` | Python 3.14 and 3.13 are the two newest stable feature releases on the decision date. Python 3.15 remains a prerelease. |
| ACP SDK | `agent-client-protocol==0.12.1` | Latest stable release, official schema models and stdio JSON-RPC runtime, supports Python 3.10–3.14. |
| Concurrency | Standard-library `asyncio` | The adapter is I/O-bound and needs explicit task, stream, timeout, and subprocess ownership. |
| Data validation | SDK Pydantic models | Keeps wire objects aligned with the canonical ACP schema. |
| Build backend | Hatchling `1.32.4` | Stable PEP 517 backend with explicit `src` layout and compact project configuration; pip owns VCS fetching. |
| Lock and environment | uv `0.12.21` | Reproducible universal lock, project-local environments, and cross-platform Python management. |
| Lint and format | Ruff `0.16.9` | One stable tool for formatting, import order, and lint rules. |
| Static typing | mypy `2.3.1` in strict mode | Stable Python-native checker; verified against the ACP SDK surface on Python 3.13 and 3.14. |
| Tests | pytest `9.1.1`, pytest-asyncio `1.4.0`, pytest-timeout `2.4.0` | Stable async test stack with hard test deadlines. |

A universal scratch lock resolved these versions together. Separate Python 3.13 and 3.14 environments imported the SDK and passed Ruff, strict mypy, and bytecode compilation of a minimal `Agent` implementation.

The SDK has `1.0.0rc2` available, but it is a prerelease. The stable `0.12.1` line remains selected until a final 1.x release has compatible API evidence and the full project suite passes.

## SDK integration

The adapter uses `acp.schema` models and stable helper builders, but it does not serve untrusted input through `acp.run_agent()` directly.

SDK `0.12.1` deliberately salvages invalid list items in fields such as `mcpServers` and `additionalDirectories`, and its default validation errors may include offending input. Its default router also treats stable ACP `session/close` as unstable and does not dispatch `$/cancel_request`. Those defaults conflict with this project's strict rejection, redaction, and cancellation contracts.

A project-owned `AcpStdioServer` therefore:

- Reads bounded UTF-8 newline-delimited JSON objects
- Validates the JSON-RPC envelope and raw method parameters before constructing SDK models
- Rejects invalid list items instead of silently dropping them
- Tracks request IDs and implements `$/cancel_request`
- Dispatches stable `session/close` without enabling unrelated unstable routes
- Serializes successful SDK models with protocol aliases
- Maps all failures to fixed, redacted JSON-RPC errors
- Ensures stdout contains no non-protocol output

The SDK remains authoritative for generated ACP v1 model shapes and output helpers. The project owns framing supervision, strict input validation, routing policy, and sanitized error serialization.

Experimental SDK modules and ACP v2 are excluded.

## Package layout

```text
pyproject.toml
uv.lock
src/agy_acp/
  __init__.py
  cli.py
  protocol.py
  agent.py
  sessions.py
  process.py
  mcp.py
  events.py
  ndjson.py
  config.py
  errors.py
tests/
  unit/
  integration/
  fixtures/
```

The public console entry point will be:

```toml
[project.scripts]
agy-acp = "agy_acp.cli:main"
```

The completed repository root will be a complete PEP 517 project. These flows must require no manual clone or build step:

```bash
pip install git+https://github.com/Kcrong/agy-acp.git
pip install git+https://github.com/Kcrong/agy-acp.git@<commit-sha>
```

## Process architecture

```text
AcpStdioServer ── strict JSON-RPC framing, routing, cancellation, redaction
    │
    ▼
AgyAgent ── ACP request and response mapping
    │
    ▼
SessionManager ── one active prompt per session
    │
    ├── McpHandoff ── isolated client-provided stdio servers
    │
    ▼
AgyProcess ── one isolated agy lifecycle per session
    │
    ▼
bounded NDJSON parser and ordered event consumer
```

### Ownership rules

- `AcpStdioServer` owns ACP input bounds, strict raw validation, request tasks, routing, and sanitized responses.
- `AgyAgent` maps validated ACP methods and never owns subprocess details.
- `SessionManager` owns admission, session identity, prompt serialization, restart, close, and disconnect cleanup.
- `McpHandoff` owns per-session stdio MCP validation, process-scoped configuration, and cleanup.
- `AgyProcess` owns one child process, stream tasks, timers, and process-tree termination.
- The NDJSON parser has no process or protocol side effects.
- Configuration is validated once before the agent begins reading ACP stdin.

Every task, stream, timer, and process has one explicit owner and one bounded cleanup path.

## Session load history gate

`agy --conversation` preserves opaque conversation identity for later prompts. A credential-safe `agy 1.2.14` resume probe sent EOF without a new prompt and observed only the structural `init` event: no history `step_update` or `result` events were emitted. This tested path does not establish the complete prior transcript replay required by ACP `session/load`.

Identity resumption is therefore used only for internal process-generation continuity. `session/load` remains unregistered and its capability remains false until an upstream interface supplies complete ordered history without exposing raw conversation data through logs or persistent adapter state.

## Stdio MCP handoff gate

ACP v1 requires stdio MCP support. Official Antigravity documentation and a live `agy 1.2.14` scratch probe confirm that workspace `.agents/mcp_config.json` starts stdio MCP servers. The CLI exposes no per-invocation MCP configuration flag.

Writing client-provided commands or environment values into the user's workspace is not acceptable: those values may be secrets, concurrent sessions need different configurations, and a crash could leave sensitive repository state behind. `McpHandoff` must prove a process-scoped, crash-safe path before the adapter claims complete ACP v1 compatibility.

The accepted handoff must:

- Avoid persistent user-workspace and global-config mutation
- Keep each session's server set and environment isolated
- Preserve the requested ACP `cwd`
- Clean up all generated state after normal exit, failure, cancellation, and host crash recovery
- Work on Linux and macOS

If no safe handoff exists in the current `agy` interface, release remains blocked on an upstream process-scoped configuration mechanism. An empty-list-only implementation must not be described as fully ACP v1 compatible.

## Cross-platform process policy

- Linux and macOS start `agy` in a new process session and signal the process group.
- Windows is not a supported platform and has no CI or process-control contract in this implementation.
- Persistent `agy` processes run without print-mode or child timeout flags; `AgyProcess` owns all deadlines.
- Graceful termination is attempted first.
- Hard termination is bounded and awaited before a lifecycle is considered closed.
- Platform-specific behavior is isolated behind one process-control interface and tested with fake descendant processes.

No platform is advertised unless its hosted matrix and process-tree tests pass.

## CI policy

CI uses only standard GitHub-hosted runners in a four-cell matrix and runs the test jobs only for pull requests whose current state is ready for review. Draft revisions and pushes to `main` do not allocate test jobs. Branch protection requires all four current-head checks before `main` can be merged:

- `ubuntu-latest`: Python 3.13 and 3.14
- `macos-latest`: Python 3.13 and 3.14

If a `latest` label points to a preview image, the workflow pins the newest stable generally available image instead.

Selected Actions:

| Action | Stable release | Commit SHA | Runtime |
| --- | --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | Node 24 |
| `actions/setup-python` | `v7.0.0` | `5fda3b95a4ea91299a34e894583c3862153e4b97` | Node 24 |
| `astral-sh/setup-uv` | `v10.2.0` | `c18668ad3cf93ea998bef934396af7bb5c839dc7` | Node 24 |

The future workflow must pin uv `0.12.21`, install from `uv.lock` with frozen resolution, grant read-only repository permissions, and expose no repository secrets to pull-request code.

## Validation layers

1. Unit tests cover pure parsing, validation, mapping, and state transitions.
2. Fake-process integration tests cover stream and subprocess behavior deterministically.
3. ACP client-to-agent end-to-end tests cover JSON-RPC framing and lifecycle behavior.
4. An opt-in real `agy` smoke test records only structural metadata and a fixed-response hash.
5. Clean Git installation tests validate import and the console entry point in an isolated consumer.
6. Hosted CI runs every supported operating-system and Python-version combination.

## Rejected alternatives

### Direct use of the SDK transport

Rejected for untrusted ACP input in SDK `0.12.1`. Its permissive item salvage, validation-error serialization, missing request-cancellation dispatch, and unstable close route do not satisfy the strict contract. The project-owned boundary remains intentionally small and continues using canonical SDK schema models.

### Full ACP schema reimplementation

Rejected because the official SDK already supplies generated canonical models and output helpers. Recreating those types would increase protocol-drift risk without improving strict input supervision.

### ACP Python SDK prerelease

Rejected until a final 1.x release is available and its migration surface passes the complete suite. The project does not adopt prerelease dependencies by default.

### Additional async or CLI frameworks

Rejected for the initial implementation. `asyncio`, the ACP SDK, and a small standard-library entry point cover the required behavior. Extra frameworks would enlarge the dependency and failure surface before a demonstrated need exists.

### Source-level TypeScript port

Rejected. The preserved TypeScript implementation is a behavioral oracle for external contracts and regression scenarios, not source material for the Python design.

## Revisit conditions

Revisit this decision when any of these changes:

- Python 3.15 reaches stable release and the ACP SDK supports it.
- A final ACP SDK 1.x release is available and migration has complete test evidence.
- A selected tool becomes unsupported, vulnerable, or incompatible with either supported Python version.
- Measured behavior requires an additional dependency or a different process-control mechanism.
