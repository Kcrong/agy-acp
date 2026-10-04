# ACP v1 bridge contract

**Status:** Accepted for the first Python implementation

## Purpose

`agy-acp` exposes the installed Antigravity CLI (`agy`) as an Agent Client Protocol v1 agent over JSON-RPC and standard I/O. This contract defines the observable behavior that implementation tests must enforce.

Primary references:

- [ACP v1 overview](https://agentclientprotocol.com/protocol/v1/overview)
- [ACP v1 schema](https://agentclientprotocol.com/protocol/v1/schema)
- [ACP v1 transports](https://agentclientprotocol.com/protocol/v1/transports)
- [Official ACP Python SDK](https://agentclientprotocol.com/libraries/python)

## Transport boundary

```text
ACP client
    │ newline-delimited UTF-8 JSON-RPC on stdin/stdout
    ▼
Python agy-acp
    │ newline-delimited agy stream-json events
    ▼
agy
```

The adapter must follow these invariants:

- Standard input accepts only ACP JSON-RPC messages.
- Standard output contains only valid ACP JSON-RPC messages.
- Diagnostics use standard error and exclude sensitive payload values.
- Every protocol message is UTF-8 and occupies one newline-delimited record.
- The adapter provides no HTTP listener, telemetry service, or independent authentication store.

## ACP surface

### Baseline

| Direction | Method | Adapter responsibility |
| --- | --- | --- |
| Client to agent request | `initialize` | Negotiate ACP v1, answer any unsupported protocol version with the latest supported version, and advertise only verified capabilities. |
| Client to agent request | `session/new` | Validate the workspace and stdio MCP specifications, register them for lazy first-prompt startup, and create an isolated `agy` session. |
| Client to agent request | `session/prompt` | Serialize supported content, run one turn, stream updates, and return one terminal stop reason. |
| Client to agent notification | `session/cancel` | Cancel the active turn and return `cancelled` from the original prompt request. |
| Agent to client notification | `session/update` | Stream ordered agent text chunks through the SDK connection. |

All agents must support the baseline session methods and notifications. `initialize` returns no unverified optional capability.

### Conditional

The following surface is advertised only after its success, rejection, cancellation, and lifecycle boundaries pass end-to-end tests:

| Method or field | Upstream support | Activation condition |
| --- | --- | --- |
| `session/resume` | `agy --conversation` resumes identity without replaying prior messages | Validate the opaque identity and working directory, register or atomically reconfigure an idle session, preserve ordered additional directories and strict stdio MCP handling, and emit no history updates. |
| `session/load` | `agy --conversation` resumes identity; the tested EOF/no-prompt path did not replay history | Do not advertise until complete ordered history replay is proven; resumption without replay is insufficient. |
| `session/close` | Explicit adapter cleanup | The project dispatcher handles the stable method without enabling unrelated SDK unstable routes; active work is cancelled before all resources are released. |
| `additionalDirectories` | Repeated `agy --add-dir` | Every path is absolute, ordered, and passed as a literal argument. |

A credential-safe `agy 1.2.14 --conversation` probe resumed a known conversation with EOF and no new prompt. It emitted one `init` event, no `step_update` or `result` history events, no standard-error bytes, and exited successfully. This establishes identity continuation without replay: the adapter implements `session/resume` and emits no history updates, while `session/load` remains unregistered and unadvertised until an upstream interface supplies the complete ordered user and agent message history.

### Session model and effort configuration

At startup the adapter executes the literal `agy models` subcommand once with bounded stdout, stderr, duration, model count, identifier length, and label length. The account-specific result is parsed as tab-separated model ID and display-name pairs; malformed, duplicate, oversized, timed-out, or unsuccessful discovery fails startup without exposing backend output. Restarting the adapter refreshes the cached list.

`session/new` and `session/resume` return two standard ACP `configOptions` selectors:

- `model`, category `model`, contains `default` followed by the discovered model variants.
- `effort`, category `thought_level`, contains only `default` while agy controls the model, then the discovered sibling effort variants after a concrete model is selected.

`session/set_config_option` returns the complete updated option state. A concrete model ID already identifies an effort variant in `agy 1.2.14`; selecting one therefore updates the displayed effort and launches the next generation with `--model <variant>`. Changing effort while a concrete model is selected moves to that model family's matching discovered variant and rejects unsupported levels. Returning the model to `default` also returns effort to `default`; the adapter does not guess which effort values agy's account-specific default model accepts. Changes during an active prompt affect the next prompt because every completed generation is retired. Explicit model launches fail closed if the `init` event reports a different model.

### Unsupported for the first implementation

- Authentication and provider methods
- Session list, delete, fork, and legacy modes
- HTTP and SSE MCP transports unless their capabilities are advertised
- Client file, terminal, permission, and elicitation calls
- Image, audio, and embedded resource prompt content
- Experimental ACP v2 and unstable SDK features

Unsupported methods are not registered or advertised. Unsupported input to a supported method returns a protocol error instead of being ignored.

## MCP compatibility

ACP v1 requires every agent to support client-provided stdio MCP servers. `agy-acp` accepts strict stdio entries containing `name`, `command`, string `args`, and name/value `env` items. Duplicate server names, duplicate environment names, malformed values, NUL bytes, unknown fields, and HTTP, SSE, or ACP transports return fixed `-32602 Invalid params` errors without payload data.

`agy 1.2.14` discovers MCP servers from `.agents/mcp_config.json` and starts them lazily after the first user prompt. The adapter provides that configuration without changing the user's workspace or global configuration:

1. A lease-owning manager creates a private `0700` owner directory and one random `0700` generation directory per `agy` process.
2. The generation's `0600` config contains only the current Python interpreter, `-E -P -m agy_acp.mcp_launcher`, and random opaque server slots. Client server names, commands, arguments, and environment values are absent.
3. Session creation resolves each target to a canonical absolute executable, using only absolute `PATH` entries for a bare command. Encoded client specifications exist only in namespaced overrides on that `agy` process environment.
4. Python environment-ignore and safe-path modes prevent workspace `sitecustomize` or `agy_acp` packages from replacing the launcher while retaining normal user-site package lookup. The launcher reads one slot, removes every internal specification variable and ambient `PYTHONHOME`/`PYTHONPATH`, applies only that server's requested environment, and replaces itself with the absolute executable and literal arguments through `execve()` without a shell.
5. The private generation directory is appended after client `additionalDirectories`, preserving the requested project as the real process working directory.
6. Cleanup retains the non-sensitive config through lazy MCP startup and removes it only after the complete `agy` process group and owned stream tasks stop. Failed cleanup remains owned and retryable; a later manager removes validated, unlocked stale owner roots.

The installed `agy` process and generated launcher processes are an explicit trusted boundary. The current upstream interface requires them to receive the process-scoped encoded specifications, so the adapter does not claim to protect MCP commands or credentials from a compromised `agy` process, a modified launcher, or arbitrary children launched directly by either. Scrubbing ensures that each final MCP target receives no internal specification variables and only its requested environment overrides.

Deterministic tests cover one and many servers, virtualenv and user-site launcher lookup, hostile workspace import hooks, active config timing, random name collision avoidance, project working-directory preservation, concurrent session and environment isolation, launcher failure, initial and in-flight spawn failure, repeated spawn cancellation and deadline, prompt success/failure/cancellation/timeout, `session/close`, client disconnect, descendant process cleanup, shared one-signal cleanup, explicit fixture-child reaping, transient zombie-only and persistent Darwin `EPERM`, parent-path substitution, bounded stale-root scavenging, and cleanup retry. Credential-safe live probes separately confirmed lazy startup, two concurrent isolated sessions, and temporary-config precedence for duplicate project server names on `agy 1.2.14`.

HTTP, SSE, and ACP MCP transports remain unsupported and unadvertised.

## Prompt content

The baseline accepts ACP text and resource-link blocks.

- Empty text blocks are removed.
- Remaining blocks preserve their original order.
- Text is passed without semantic rewriting.
- A resource link becomes an explicit textual reference containing its display name and URI.
- Blocks are separated with two newline characters.
- A prompt with no usable block is rejected before an `agy` process receives input.

Image, audio, and embedded resource blocks are rejected until their capabilities are intentionally implemented and advertised.

## `agy` stream-json contract

The current compatibility baseline is `agy 1.2.14`. The persistent adapter invocation is:

```text
agy [--model <variant>] --input-format stream-json --output-format stream-json
```

Prompts are written only as stdin `user` events. The adapter does not pass `--print` or `--print-timeout`: print mode is for a single command-line prompt, and an upstream print timeout may return partial output as success. The adapter exclusively owns initialization and prompt deadlines. Internal process-generation continuity and ACP `session/resume` add `--conversation <opaque-id>`, additional directories add repeated `--add-dir <absolute-path>` arguments, and an optional discovered model variant is passed as one literal argument pair without a shell.

The adapter writes one user event per prompt:

```json
{"event":"user","message":{"role":"user","content":[{"type":"text","text":"<prompt>"}]}}
```

A successful observed turn followed this sequence:

```text
init → step_update* → result
```

Only structural information was retained from the probe. Response text, conversation identifiers, usage values, environment values, and standard-error contents were not recorded.

### Event mapping

| `agy` event | ACP behavior |
| --- | --- |
| `init` | Capture the opaque conversation identifier internally; emit no update. |
| `step_update` with `text_delta` | Send an ordered `agent_message_chunk` text update. |
| `step_update` without `text_delta` | Advance internal lifecycle only. |
| `result` with `status=SUCCESS` | Return `PromptResponse(stop_reason="end_turn")`. |
| `result` with `status=CANCELED`, or cancellation-related `ERROR` after adapter cancellation | Return `PromptResponse(stop_reason="cancelled")`. |
| `INTERRUPTED` after adapter cancellation | Return `PromptResponse(stop_reason="cancelled")`. |
| Unsolicited `INTERRUPTED`, or `ERROR`, `INVALID`, `WAITING`, or `RUNNING` | Return `-32010 Backend unavailable`; a terminal result must not remain nonterminal or ambiguous. |
| Unknown status value | Fail closed with `-32010 Backend unavailable`. |
| Unknown event | Ignore it without logging the raw event or payload. |
| Malformed record, premature EOF, or abnormal exit | Fail the turn with a typed bridge error and retire the process. |

If the final response starts with the exact text already emitted as deltas, only the missing suffix is emitted. If no delta was emitted, the final response is emitted once. A conflicting final response fails closed rather than duplicating or replacing streamed content.

### Event identity and ordering

Known events are accepted only in the session phase where they are valid.

- Exactly one `init` event must arrive before any known update or result.
- The top-level `init.conversation_id` becomes the session's opaque backend identity.
- Every `step_update.conversation_id` and `result.conversation_id` must match that identity.
- A mismatched or missing identifier retires the process and fails the turn.
- `step_update` is accepted only during initialization or an active prompt according to its documented state.
- Exactly one terminal `result` is accepted for each prompt.
- Duplicate or late `init`, update after terminal result, result without an active prompt, and multiple results fail closed.
- Process exit and stdout EOF are not terminal success until queued event handling and the final response barrier complete.

## Session and process lifecycle

- Each ACP session owns independent conversation metadata and an `agy` process lifecycle.
- `session/resume` validates the requested identity and working directory through `agy --conversation`, or atomically reconfigures an existing idle session, without emitting prior-message updates.
- A session permits at most one active prompt.
- Different sessions may run concurrently.
- Active and starting sessions count against one bounded admission limit.
- An idle process may exit; the next prompt may restart it once with the opaque conversation identifier.
- Closing a session or the ACP connection performs bounded cleanup of the `agy` process group.
- MCP servers must remain in the inherited process group; daemonizing or detached descendants are unsupported and outside the lifecycle guarantee.
- A closed or unknown session returns a stable session error.

### Cancellation and timeout

Cancellation is a terminal race with exactly one winner.

1. Mark the prompt as cancelling.
2. Signal the complete process group on Linux and macOS.
3. Wait for a structured result or exit during a bounded grace period.
4. Join one shared cleanup task that sends hard termination at most once and polls for process-group disappearance.
5. Treat only `ESRCH` as terminal proof; transient Darwin `EPERM` remains pending, while persistent `EPERM` or a live group at the deadline fails closed without releasing owned MCP state.
6. Complete the original ACP prompt with `cancelled`.

A prompt timeout uses the same shutdown path but returns a timeout error. Session cancellation returns a valid `PromptResponse` with `stopReason=cancelled`. Request-level `$/cancel_request` cancels the matching request task and returns error `-32800` for that request. Session cancellation, request cancellation, timeout, process exit, and normal result must never complete the same prompt more than once.

## Error mapping

Error messages are fixed sentences. Error data never contains raw parameters, Pydantic validation input, prompts, responses, conversation identifiers, environment values, or stderr.

| Condition | Code | Message |
| --- | ---: | --- |
| Malformed JSON | `-32700` | `Parse error` |
| Invalid JSON-RPC envelope or oversized ACP record | `-32600` | `Invalid request` |
| Unregistered method | `-32601` | `Method not found` |
| Invalid path, parameter, prompt block, MCP transport, or list item | `-32602` | `Invalid params` |
| Request-level cancellation | `-32800` | `Request cancelled` |
| Unexpected invariant or handler failure | `-32603` | `Internal error` |
| `agy` cannot start or terminates without a valid result | `-32010` | `Backend unavailable` |
| Initialization deadline | `-32011` | `Initialization timed out` |
| Prompt deadline | `-32012` | `Prompt timed out` |
| Overlapping prompt in one session | `-32013` | `Session busy` |
| Session admission limit | `-32014` | `Session capacity exceeded` |
| Unknown or closed session | `-32015` | `Session not found` |

An `agy` structured execution error uses `-32010` with the same generic message unless a more specific adapter condition applies.

## Resource and security limits

Central configuration must bound at least:

- ACP and `agy` line size
- Model discovery duration, stdout/stderr bytes, model count, identifier length, and label length
- Retained standard-error bytes
- Active plus starting sessions
- MCP server, argument, environment-item, per-spec, total-spec, and process-environment override sizes
- Initialization timeout
- Prompt timeout
- Graceful cancellation time
- Hard-kill cleanup time

Configuration values must be positive safe integers. Invalid values fail at startup without echoing the supplied value.

The adapter must not:

- Read or modify `agy` credential files
- Emit, log, persist, or include inherited environment values in errors
- Enable dangerous permission bypass flags
- Resolve an executable through a client-controlled working directory
- Pass user input through a shell command string
- Include prompts, responses, conversation identifiers, usage, or raw stderr in default errors

## Compatibility matrix

Every listed row needs an automated test before the first release.

| Area | Required cases |
| --- | --- |
| ACP framing | Valid request and notification, malformed JSON, wrong `jsonrpc`, missing or invalid ID/method, embedded newline, oversized record, EOF remainder |
| Dispatch | Baseline methods, `session/set_config_option`, unknown method, notification without response, stable `session/close`, unsupported optional methods |
| Initialization | Compatible version, unsupported lower v1-shaped and higher canonical v2-shaped versions, truthful stable capabilities, bounded model discovery success/failure/timeout/cancellation, invalid capability input, init timeout, early exit |
| MCP | Empty list; one and many stdio servers; invalid command/args/env and duplicate names; lazy first-prompt startup; opaque project-name collision avoidance; concurrent environment isolation; cwd and additional-directory order; initial/launcher failure; success/cancel/timeout/close/disconnect cleanup; descendant cleanup; stale lease and cleanup retry; HTTP/SSE/ACP rejection; no persisted secret values |
| Prompt input | Text, resource link, mixed ordering, empty prompt, unsupported block, invalid paths, strict invalid-list rejection |
| Streaming | One delta, many deltas, split records, slow consumer, final suffix, no-delta final response, conflicting final response, response barrier |
| `agy` ordering | Duplicate/late init, update before init, missing/mismatched conversation ID, update outside active turn, duplicate/late result |
| Terminal outcomes | `SUCCESS`, `ERROR`, `CANCELED`, `INTERRUPTED`, `INVALID`, `WAITING`, `RUNNING`, unknown status, malformed `agy` JSON, oversized backend record, premature stdout EOF, non-zero exit, unknown event, exit while events are queued |
| Cancellation | Session cancel, request cancel with `-32800`, timeout, cancel/result race, cancel/exit race, ignored graceful signal, hard-kill barrier |
| Sessions | Capacity, concurrent sessions, duplicate prompt, idle restart, startup retirement, external and existing-session resume, resume identity/cwd rejection, resume concurrency, disconnect cleanup, unknown session |
| Configuration | Model listing parse and bounds, default model/effort, exact model selection, family effort transitions, unsupported values, resume preservation, active-prompt next-turn application, literal argv propagation, selected-model init verification |
| Conditional surface | Resume without history, complete ordered load-history replay upstream gate, close idle/active/starting/duplicate, additional-directory ordering and validation |
| Errors and redaction | Every fixed code/message, invalid params containing sensitive sentinel values, internal exception, stderr exclusion, no raw Pydantic errors |
| Platforms | Linux process group, macOS process group |
| Runtimes | Python 3.13 and Python 3.14 |
| Compatibility kit | ACP Test Compatibility Kit plus adapter-specific regressions |

The fake-`agy` end-to-end suite is the primary deterministic oracle. A supplementary Python 3.14 gate runs the official experimental [ACP TCK](https://github.com/agentclientprotocol/acp-tck) at commit `9b4334813bc4a3583285592027cc2f3ab85793bd`, version `0.2.0`, against vendored schema revision `6d08f412a7a1370d3cc9a124e3be3d6acf92641e`. Its JSON report must return exit code zero, a conformant verdict, no authentication or version blocker, the exact 56-entry pinned ID/tier registry with consistent verdict counts, all 21 mandatory requirements as `PASS`, and every requirement for advertised `session/resume`, `session/close`, and `additionalDirectories` capabilities as `PASS` rather than skipped. The report is retained as a CI artifact.

The TCK is experimental, does not certify ACP conformance, and does not cover every MCP, terminal, filesystem, redaction, or process-lifecycle boundary. It therefore cannot replace project-specific regressions. An opt-in real `agy` smoke test separately verifies event shape and a fixed response hash without printing sensitive values.
