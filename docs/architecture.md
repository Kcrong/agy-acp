# Python architecture decision

**Status:** Accepted on 2026-10-01 with process-scoped stdio MCP handoff

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
| Version source | Hatch VCS `0.5.0` | Derives PEP 440 package metadata from immutable release tags without a separately maintained version field. |
| Lock and environment | uv `0.12.21` | Reproducible universal lock, project-local environments, and cross-platform Python management. |
| Lint and format | Ruff `0.16.9` | One stable tool for formatting, import order, and lint rules. |
| Static typing | mypy `2.3.1` in strict mode | Stable Python-native checker; verified against the ACP SDK surface on Python 3.13 and 3.14. |
| Tests | pytest `9.1.1`, pytest-asyncio `1.4.0`, pytest-timeout `2.4.0` | Stable async test stack with hard test deadlines. |
| ACP TCK | `0.2.0` at commit `9b4334813bc4a3583285592027cc2f3ab85793bd`, Python 3.14 only | Official experimental supplementary compatibility evidence; not a runtime dependency or certification. |

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
- Dispatches stable `session/close` and `session/resume` without enabling unrelated unstable routes
- Negotiates unsupported lower or higher protocol versions to the latest supported stable version while strictly distinguishing canonical v1 and v2-shaped initialization fields
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
  mcp_launcher.py
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
- `AgyProcess` owns one child process, stream tasks, timers, and process-group termination.
- The NDJSON parser has no process or protocol side effects.
- Configuration is validated once before the agent begins reading ACP stdin.

Every task, stream, timer, and process has one explicit owner and one bounded cleanup path.

## Session resume and load history gate

`agy --conversation` preserves opaque conversation identity for later prompts. The adapter uses that supported behavior for `session/resume`: a fresh adapter validates the requested identity and working directory through a bounded `agy` initialization, while an existing idle session is atomically reconfigured with the request's complete additional-directory and stdio MCP lists. Resume returns no prior-message updates.

A credential-safe `agy 1.2.14` resume probe sent EOF without a new prompt and observed only the structural `init` event: no history `step_update` or `result` events were emitted. This does not establish the complete prior transcript replay required by ACP `session/load`. `session/load` therefore remains unregistered and its capability remains false until a supported upstream interface supplies every historical user and agent message with exact content, ordering, boundaries, and a replay-complete signal.

## Stdio MCP handoff

ACP client stdio MCP entries are parsed into immutable bounded specifications. HTTP, SSE, and ACP transports remain unsupported. `agy 1.2.14` has no per-invocation MCP flag and starts workspace-configured MCP servers lazily at the first prompt, so the handoff uses a private additional directory rather than writing client data into the project.

A process-scoped generation has two channels:

- A `0600` `.agents/mcp_config.json` contains collision-resistant opaque server keys and invokes the installed interpreter with `-E -P -m agy_acp.mcp_launcher <slot>`. It contains no client server name, target command, arguments, environment name, or environment value.
- Namespaced environment overrides on that `agy` process contain the encoded bounded specifications. Session creation resolves targets to canonical absolute executables through absolute `PATH` entries. Python environment-ignore and safe-path modes prevent workspace import hooks from replacing the launcher while retaining normal user-site lookup; it removes every internal specification variable and ambient `PYTHONHOME`/`PYTHONPATH`, applies only its target environment, and calls `execve()` without a shell.

The lease-owning manager canonicalizes a private-owner or sticky temp parent, keeps parent and owner directory descriptors open, creates descendants relative to those descriptors, and revalidates public path device/inode identity before exposing or removing a generation. Owner and generation directories use mode `0700`. Stale scavenging examines at most 128 directory entries and processes only exact-shape owner names, opens directories and leases without following links, rejects nonregular or incorrectly owned/mode leases, locks nonblocking, and revalidates the directory inode before removal. Client additional directories retain their order and the private config root is appended last, while `agy` continues to run in the requested project working directory. Random generation roots, config keys, and slots isolate concurrent processes and avoid collisions with project MCP names.

The installed `agy` backend and generated launcher processes are an explicit trusted boundary. The current config-launch interface requires them to receive the encoded specifications, so a compromised backend, modified launcher, or arbitrary child either launches directly can inspect them. Scrubbing ensures only that each final MCP target receives no internal specification variables and only its requested environment overrides.

Because startup is lazy, a generation config remains until its `agy` process ends. Subprocess creation runs under the initialization or remaining prompt deadline in a non-raising supervisor, so cancellation waits only for a bounded structural outcome and any successfully created process transfers to normal cleanup ownership. `AgyProcess` owns a once-only cleanup callback as part of its close barrier: it runs only after process-group and stream-task cleanup, and failure leaves the process unclosed and retained for retry. Agent shutdown closes processes before the manager; unlocked stale owner directories are scavenged later. Residual files are non-sensitive because client specifications are never written there, and the process-scoped environment disappears when its process or host terminates.

Deterministic tests cover success, startup and launcher failure, cancellation, timeout, explicit session close, disconnect, concurrent isolation, process descendants, cleanup retry, and stale leases. Credential-safe `agy 1.2.14` probes additionally verify first-prompt timing and duplicate-name precedence.

## Cross-platform process policy

- Linux and macOS start `agy` in a new process session and signal the process group.
- Windows is not a supported platform and has no CI or process-control contract in this implementation.
- Persistent `agy` processes run without print-mode or child timeout flags; `AgyProcess` owns all deadlines.
- Graceful termination is attempted first.
- One shared cleanup task owns the destructive process-group signal across explicit shutdown and root-exit observation; concurrent callers join it rather than signaling the PGID again.
- Group cleanup treats only `ESRCH` as proof that the group disappeared. Darwin `EPERM` remains pending because a zombie-only group can produce it; persistent `EPERM` or a still-live group at the deadline fails closed with `BackendShutdownError`.
- Hard termination and disappearance verification are bounded and awaited before a lifecycle is considered closed.
- Platform-specific behavior is isolated behind one process-control interface and tested with fake descendants that remain in the inherited group.
- MCP servers that daemonize, call `setsid()`, or otherwise leave the inherited process group are unsupported and outside the cleanup guarantee.

No platform is advertised unless its hosted matrix and process-group tests pass.

## CI policy

CI uses only standard GitHub-hosted runners in a four-cell matrix and runs the test jobs for pull requests whose current state is ready for review and for every push to `main`. Draft revisions do not allocate test jobs. Branch protection requires all four current-head checks before `main` can be merged, and only a successful `main` run can enter automatic tagging and publication:

- `ubuntu-latest`: Python 3.13 and 3.14
- `macos-latest`: Python 3.13 and 3.14

Required status checks are bound to the GitHub Actions app and enforced for administrators. The protection policy intentionally requires no human approval; repository write access is therefore a trusted boundary, and a trusted writer can propose workflow changes that alter what those checks execute.

If a `latest` label points to a preview image, the workflow pins the newest stable generally available image instead.

Selected Actions:

| Action | Stable release | Commit SHA | Runtime |
| --- | --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | Node 24 |
| `actions/setup-python` | `v7.0.0` | `5fda3b95a4ea91299a34e894583c3862153e4b97` | Node 24 |
| `astral-sh/setup-uv` | `v10.2.0` | `c18668ad3cf93ea998bef934396af7bb5c839dc7` | Node 24 |
| `actions/upload-artifact` | `v7.0.1` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | Node 24 |
| `actions/download-artifact` | `v8.0.1` | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | Node 24 |
| `amannn/action-semantic-pull-request` | `v6.1.1` | `48f256284bd46cdaab1048c3721360e808335d50` | Node 24 |
| `mathieudutour/github-tag-action` | `v7.0.0` | `af99e60ce8132224b8e6ebab5023449fe256ed46` | Node 24 |
| `pypa/gh-action-pypi-publish` | `v1.14.2` | `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` | Composite/container |

The workflow pins uv `0.12.21`, installs from `uv.lock` with frozen resolution, grants read-only repository permissions, and exposes no repository secrets to pull-request code. A separate `pull_request_target` workflow validates only PR metadata and never checks out contributor code. Squash-only repository settings default the `main` commit subject to the PR title plus its number; the release workflow independently queries every associated merged PR and requires that exact generated subject before tagging. The Ubuntu/Python 3.14 cell also runs the pinned experimental ACP TCK and retains its JSON report. The macOS/Python 3.13 cell repeats concurrent MCP cleanup 20 times to exercise Darwin process-group retirement. Every cell builds distributions, runs Twine strict metadata checks, validates exact wheel/sdist manifests, and installs the wheel in a clean environment.

`github-tag-action` v7 is a recent major selected after reviewing its Node 24 migration, Conventional Commits parser unification, complete tag pagination, failure behavior, and passing upstream Ubuntu/macOS/Windows test matrix. Its immutable commit pin bounds adoption risk, and its history aggregation preserves the highest required bump when multiple tested commits are released together.

The `publish.yml` workflow accepts only a successful `main` push result from the repository's `CI` workflow, confirms the tested commit remains in `main` history, and allows one active release while retaining up to 100 waiting runs with `queue: max`. If a newer release tag already contains that commit, the run exits without creating a backward tag; otherwise the preceding release tag must be its ancestor. For every commit after that tag, the workflow verifies the commit subject against the associated merged PR title before aggregating Conventional Commit bumps. It calculates the expected tag before creation and permits a retry to reuse an existing tag only when that tag is the same annotated version on the same commit. Hatch VCS derives package metadata from the exact tag. One Ubuntu build validates and records the immutable distributions. A non-privileged preflight then compares PyPI release filenames and SHA-256 hashes: complete matching releases are no-ops, partial matching releases stage only missing files, and conflicts fail closed. That filtered immutable workflow artifact crosses into the protected `pypi` job, which executes only the SHA-pinned download and Trusted Publishing Actions; it does not check out or execute repository code while `id-token: write` is available. A post-preflight race therefore fails closed and a later serialized retry replans safely. The environment accepts protected branches, requires `Kcrong` as its sole reviewer, and allows self-review so the sole maintainer can release; the repository administrator account is therefore an explicit publication trust boundary. Only the tag job receives `contents: write`, only the final publish job receives `id-token: write`, and no password or repository secret is used. Human environment approval remains required before the irreversible upload.

## Validation layers

1. Unit tests cover pure parsing, validation, mapping, and state transitions.
2. Fake-process integration tests cover stream and subprocess behavior deterministically.
3. ACP client-to-agent end-to-end tests cover JSON-RPC framing and lifecycle behavior.
4. A pinned official experimental ACP TCK verifies every mandatory and advertised capability requirement it covers and retains a machine-readable report.
5. An opt-in real `agy` smoke test records only structural metadata and a fixed-response hash.
6. Clean Git installation tests validate import and the console entry point in an isolated consumer.
7. Exact wheel/sdist manifest, metadata, Twine, and clean-wheel-install tests validate release artifacts.
8. Hosted CI runs every supported operating-system and Python-version combination.

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
