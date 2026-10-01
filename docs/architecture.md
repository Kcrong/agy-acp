# Python architecture decision

**Status:** Accepted on 2026-10-01

## Decision

Build `agy-acp` as a Python package using the official stable ACP Python SDK and the standard-library asynchronous process APIs. The package supports Python 3.13 and 3.14 on Linux, macOS, and Windows.

The implementation is a protocol and subprocess adapter. It does not implement a model provider, credential store, network service, or terminal UI.

## Runtime stack

| Component | Selected version or policy | Reason |
| --- | --- | --- |
| Python | `>=3.13,<3.15` | Python 3.14 and 3.13 are the two newest stable feature releases on the decision date. Python 3.15 remains a prerelease. |
| ACP SDK | `agent-client-protocol==0.12.1` | Latest stable release, official schema models and stdio JSON-RPC runtime, supports Python 3.10–3.14. |
| Concurrency | Standard-library `asyncio` | The adapter is I/O-bound and needs explicit task, stream, timeout, and subprocess ownership. |
| Data validation | SDK Pydantic models | Keeps wire objects aligned with the canonical ACP schema. |
| Build backend | Hatchling `1.32.4` | Stable PEP 517 backend with direct Git installation support and a small project configuration. |
| Lock and environment | uv `0.12.21` | Reproducible universal lock, project-local environments, and cross-platform Python management. |
| Lint and format | Ruff `0.16.9` | One stable tool for formatting, import order, and lint rules. |
| Static typing | mypy `2.3.1` in strict mode | Stable Python-native checker; verified against the ACP SDK surface on Python 3.13 and 3.14. |
| Tests | pytest `9.1.1`, pytest-asyncio `1.4.0`, pytest-timeout `2.4.0` | Stable async test stack with hard test deadlines. |

A universal scratch lock resolved these versions together. Separate Python 3.13 and 3.14 environments imported the SDK and passed Ruff, strict mypy, and bytecode compilation of a minimal `Agent` implementation.

The SDK has `1.0.0rc2` available, but it is a prerelease. The stable `0.12.1` line remains selected until a final 1.x release has compatible API evidence and the full project suite passes.

## SDK integration

The adapter subclasses `acp.Agent` and is served by `acp.run_agent()` over stdio. The SDK provides:

- Generated Pydantic ACP v1 schema models
- JSON-RPC framing and dispatch
- Cross-platform ACP stdin/stdout plumbing
- Async agent method interfaces
- An agent-side connection for `session/update` notifications

The adapter remains responsible for:

- Capability policy
- ACP content conversion
- Session admission and ownership
- `agy` executable resolution and invocation
- NDJSON parsing and event validation
- Backpressure and event ordering
- Timeout and process-tree termination
- Stable error mapping and redaction

Experimental SDK modules and ACP v2 are excluded.

## Package layout

```text
pyproject.toml
uv.lock
src/agy_acp/
  __init__.py
  cli.py
  agent.py
  sessions.py
  process.py
  events.py
  ndjson.py
  config.py
  errors.py
tests/
  unit/
  integration/
  fixtures/
```

The public console entry point is:

```toml
[project.scripts]
agy-acp = "agy_acp.cli:main"
```

The repository root is a complete PEP 517 project. These flows must require no manual clone or build step:

```bash
pip install git+https://github.com/Kcrong/agy-acp.git
pip install git+https://github.com/Kcrong/agy-acp.git@<commit-sha>
```

## Process architecture

```text
acp.run_agent
    │
    ▼
AgyAgent
    │
    ▼
SessionManager ── one active prompt per session
    │
    ▼
AgyProcess ── one isolated agy lifecycle per session
    │
    ▼
bounded NDJSON parser and ordered event consumer
```

### Ownership rules

- `AgyAgent` maps ACP methods and never owns subprocess details.
- `SessionManager` owns admission, session identity, prompt serialization, restart, close, and disconnect cleanup.
- `AgyProcess` owns one child process, stream tasks, timers, and process-tree termination.
- The NDJSON parser has no process or protocol side effects.
- Configuration is validated once before the agent begins reading ACP stdin.

Every task, stream, timer, and process has one explicit owner and one bounded cleanup path.

## Cross-platform process policy

- Linux and macOS start `agy` in a new process session and signal the process group.
- Windows starts `agy` in a new process group and uses a tested process-tree termination strategy.
- Graceful termination is attempted first.
- Hard termination is bounded and awaited before a lifecycle is considered closed.
- Platform-specific behavior is isolated behind one process-control interface and tested with fake descendant processes.

No platform is advertised unless its hosted matrix and process-tree tests pass.

## CI policy

CI uses only standard GitHub-hosted runners in a six-cell matrix:

- `ubuntu-latest`: Python 3.13 and 3.14
- `macos-latest`: Python 3.13 and 3.14
- `windows-latest`: Python 3.13 and 3.14

If a `latest` label points to a preview image, the workflow pins the newest stable generally available image instead.

Selected Actions:

| Action | Stable release | Commit SHA | Runtime |
| --- | --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | Node 24 |
| `actions/setup-python` | `v7.0.0` | `5fda3b95a4ea91299a34e894583c3862153e4b97` | Node 24 |
| `astral-sh/setup-uv` | `v10.2.0` | `c18668ad3cf93ea998bef934396af7bb5c839dc7` | Node 24 |

The workflow pins uv `0.12.21`, installs from `uv.lock` with frozen resolution, grants read-only repository permissions, and does not expose repository secrets to pull-request code.

## Validation layers

1. Unit tests cover pure parsing, validation, mapping, and state transitions.
2. Fake-process integration tests cover stream and subprocess behavior deterministically.
3. ACP client-to-agent end-to-end tests cover JSON-RPC framing and lifecycle behavior.
4. An opt-in real `agy` smoke test records only structural metadata and a fixed-response hash.
5. Clean Git installation tests validate import and the console entry point in an isolated consumer.
6. Hosted CI runs every supported operating-system and Python-version combination.

## Rejected alternatives

### Manual JSON-RPC implementation

Rejected because the official SDK already supplies canonical schema models, framing, and cross-platform stdio behavior. Reimplementing those layers would increase protocol-drift and validation risk without improving the adapter boundary.

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
