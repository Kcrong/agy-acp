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
| Client to agent request | `initialize` | Negotiate ACP v1 and advertise only verified capabilities. |
| Client to agent request | `session/new` | Validate the workspace and create an isolated `agy` session. |
| Client to agent request | `session/prompt` | Serialize supported content, run one turn, stream updates, and return one terminal stop reason. |
| Client to agent notification | `session/cancel` | Cancel the active turn and return `cancelled` from the original prompt request. |
| Agent to client notification | `session/update` | Stream ordered agent text chunks through the SDK connection. |

All agents must support the baseline session methods and notifications. `initialize` returns no unverified optional capability.

### Conditional

The following surface is advertised only after its success, rejection, cancellation, and lifecycle boundaries pass end-to-end tests:

| Method or field | Upstream support | Activation condition |
| --- | --- | --- |
| `session/load` | `agy --conversation` | The conversation resumes and its complete prior history is replayed as ACP updates; resumption without history replay is insufficient. |
| `session/close` | Explicit adapter cleanup | Active work is cancelled and all resources are released before the response. |
| `additionalDirectories` | Repeated `agy --add-dir` | Every path is absolute, ordered, and passed as a literal argument. |

### Unsupported for the first implementation

- Authentication and provider methods
- Session list, delete, fork, resume, modes, and configuration options
- Client-provided MCP servers
- Client file, terminal, permission, and elicitation calls
- Image, audio, and embedded resource prompt content
- Experimental ACP v2 and unstable SDK features

Unsupported methods are not registered or advertised. Unsupported input to a supported method returns a protocol error instead of being ignored.

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

The current compatibility baseline is `agy 1.2.14`. A credential-safe live probe on 2026-10-01 verified these flags:

- `--input-format stream-json`
- `--output-format stream-json`
- `--print`
- `--print-timeout`
- `--conversation`
- `--add-dir`

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
| Cancellation-related `ERROR` result after adapter cancellation | Return `PromptResponse(stop_reason="cancelled")`. |
| Other `ERROR` result | Return a typed execution error. |
| Unknown event | Ignore it without logging the raw event or payload. |
| Malformed record, premature EOF, or abnormal exit | Fail the turn with a typed bridge error and retire the process. |

If the final response starts with the exact text already emitted as deltas, only the missing suffix is emitted. If no delta was emitted, the final response is emitted once. A conflicting final response fails closed rather than duplicating or replacing streamed content.

## Session and process lifecycle

- Each ACP session owns independent conversation metadata and an `agy` process lifecycle.
- A session permits at most one active prompt.
- Different sessions may run concurrently.
- Active and starting sessions count against one bounded admission limit.
- An idle process may exit; the next prompt may restart it once with the opaque conversation identifier.
- Closing a session or the ACP connection performs bounded process-tree cleanup.
- A closed or unknown session returns a stable session error.

### Cancellation and timeout

Cancellation is a terminal race with exactly one winner.

1. Mark the prompt as cancelling.
2. Signal the complete process group on Linux and macOS, or the process tree on Windows.
3. Wait for a structured result or exit during a bounded grace period.
4. Escalate to a hard process-tree termination if needed.
5. Await a bounded cleanup barrier.
6. Complete the original ACP prompt with `cancelled`.

A prompt timeout uses the same shutdown path but returns a timeout error. Request-level `$/cancel_request`, session cancellation, timeout, process exit, and normal result must never complete the same prompt more than once.

## Resource and security limits

Central configuration must bound at least:

- ACP and `agy` line size
- Retained standard-error bytes
- Active plus starting sessions
- Initialization timeout
- Prompt timeout
- Graceful cancellation time
- Hard-kill cleanup time

Configuration values must be positive safe integers. Invalid values fail at startup without echoing the supplied value.

The adapter must not:

- Read or modify `agy` credential files
- Enumerate or log the inherited environment
- Enable dangerous permission bypass flags
- Resolve an executable through a client-controlled working directory
- Pass user input through a shell command string
- Include prompts, responses, conversation identifiers, usage, or raw stderr in default errors

## Compatibility matrix

Every listed row needs an automated test before the first release.

| Area | Required cases |
| --- | --- |
| Initialization | Compatible version, incompatible version, truthful capabilities, init timeout, early exit |
| Input | Text, resource link, empty prompt, unsupported block, non-empty MCP list, invalid paths |
| Streaming | One delta, many deltas, split records, slow consumer, final suffix, conflicting final response |
| Terminal outcomes | Success, structured error, malformed JSON, premature EOF, non-zero exit, unknown event |
| Cancellation | Session cancel, request cancel, timeout, cancel/result race, cancel/exit race, ignored graceful signal |
| Sessions | Capacity, concurrent sessions, duplicate prompt, idle restart, close during startup, disconnect cleanup |
| Platforms | Linux process group, macOS process group, Windows process tree |
| Runtimes | Python 3.13 and Python 3.14 |

The fake-`agy` end-to-end suite is the primary deterministic oracle. An opt-in real `agy` smoke test verifies event shape and a fixed response hash without printing sensitive values.
