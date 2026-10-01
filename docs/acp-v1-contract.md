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
| Client to agent request | `session/new` | Validate the workspace, connect every requested stdio MCP server, and create an isolated `agy` session. |
| Client to agent request | `session/prompt` | Serialize supported content, run one turn, stream updates, and return one terminal stop reason. |
| Client to agent notification | `session/cancel` | Cancel the active turn and return `cancelled` from the original prompt request. |
| Agent to client notification | `session/update` | Stream ordered agent text chunks through the SDK connection. |

All agents must support the baseline session methods and notifications. `initialize` returns no unverified optional capability.

### Conditional

The following surface is advertised only after its success, rejection, cancellation, and lifecycle boundaries pass end-to-end tests:

| Method or field | Upstream support | Activation condition |
| --- | --- | --- |
| `session/load` | `agy --conversation` resumes identity; the tested EOF/no-prompt path did not replay history | Do not advertise until complete ordered history replay is proven; resumption without replay is insufficient. |
| `session/close` | Explicit adapter cleanup | The project dispatcher handles the stable method without enabling unrelated SDK unstable routes; active work is cancelled before all resources are released. |
| `additionalDirectories` | Repeated `agy --add-dir` | Every path is absolute, ordered, and passed as a literal argument. |

A credential-safe `agy 1.2.14 --conversation` probe resumed a known conversation with EOF and no new prompt. It emitted one `init` event, no `step_update` or `result` history events, no standard-error bytes, and exited successfully. This observed path does not establish the complete ordered replay required by ACP, so the adapter leaves `session/load` unregistered and unadvertised.

### Unsupported for the first implementation

- Authentication and provider methods
- Session list, delete, fork, resume, modes, and configuration options
- HTTP and SSE MCP transports unless their capabilities are advertised
- Client file, terminal, permission, and elicitation calls
- Image, audio, and embedded resource prompt content
- Experimental ACP v2 and unstable SDK features

Unsupported methods are not registered or advertised. Unsupported input to a supported method returns a protocol error instead of being ignored.

## MCP compatibility gate

ACP v1 requires every agent to support client-provided stdio MCP servers. HTTP and SSE remain capability-gated.

`agy 1.2.14` has no per-invocation MCP configuration flag. Official Antigravity documentation identifies `.agents/mcp_config.json` as the workspace configuration, and a credential-safe scratch probe confirmed that `agy 1.2.14` starts a stdio server from that file. Persisting an ACP client's server command or environment values into the user's workspace would expose secrets, race concurrent sessions, and mutate repository state.

The implementation must therefore prove a process-scoped handoff that:

- Connects every requested stdio server to the corresponding `agy` session
- Does not persist client-provided commands, arguments, or environment values in the user's workspace or global configuration
- Keeps concurrent sessions isolated
- Restores no shared file and leaves no sensitive crash residue
- Works on Linux and macOS

The project must not claim complete ACP v1 compatibility until this gate passes. If the current `agy` interface cannot provide a safe handoff, the limitation remains a release blocker rather than being hidden behind an empty-list-only implementation.

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
agy --input-format stream-json --output-format stream-json
```

Prompts are written only as stdin `user` events. The adapter does not pass `--print` or `--print-timeout`: print mode is for a single command-line prompt, and an upstream print timeout may return partial output as success. The adapter exclusively owns initialization and prompt deadlines. Internal process-generation resumption adds `--conversation <opaque-id>`, and additional directories add repeated `--add-dir <absolute-path>` arguments.

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
- A session permits at most one active prompt.
- Different sessions may run concurrently.
- Active and starting sessions count against one bounded admission limit.
- An idle process may exit; the next prompt may restart it once with the opaque conversation identifier.
- Closing a session or the ACP connection performs bounded process-tree cleanup.
- A closed or unknown session returns a stable session error.

### Cancellation and timeout

Cancellation is a terminal race with exactly one winner.

1. Mark the prompt as cancelling.
2. Signal the complete process group on Linux and macOS.
3. Wait for a structured result or exit during a bounded grace period.
4. Escalate to a hard process-tree termination if needed.
5. Await a bounded cleanup barrier.
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
| ACP framing | Valid request and notification, malformed JSON, wrong `jsonrpc`, missing or invalid ID/method, embedded newline, oversized record, EOF remainder |
| Dispatch | Baseline methods, unknown method, notification without response, stable `session/close`, unsupported optional methods |
| Initialization | Compatible version, incompatible version, truthful capabilities, invalid capability input, init timeout, early exit |
| MCP | Empty list, one and many stdio servers, invalid command/args/env item, per-session isolation, concurrent sessions, startup failure, HTTP/SSE rejection, no persisted secret values |
| Prompt input | Text, resource link, mixed ordering, empty prompt, unsupported block, invalid paths, strict invalid-list rejection |
| Streaming | One delta, many deltas, split records, slow consumer, final suffix, no-delta final response, conflicting final response, response barrier |
| `agy` ordering | Duplicate/late init, update before init, missing/mismatched conversation ID, update outside active turn, duplicate/late result |
| Terminal outcomes | `SUCCESS`, `ERROR`, `CANCELED`, `INTERRUPTED`, `INVALID`, `WAITING`, `RUNNING`, unknown status, malformed `agy` JSON, oversized backend record, premature stdout EOF, non-zero exit, unknown event, exit while events are queued |
| Cancellation | Session cancel, request cancel with `-32800`, timeout, cancel/result race, cancel/exit race, ignored graceful signal, hard-kill barrier |
| Sessions | Capacity, concurrent sessions, duplicate prompt, idle restart, startup retirement, disconnect cleanup, unknown session |
| Conditional surface | Complete ordered load-history replay, load failure/cancel, close idle/active/starting/duplicate, additional-directory ordering and validation |
| Errors and redaction | Every fixed code/message, invalid params containing sensitive sentinel values, internal exception, stderr exclusion, no raw Pydantic errors |
| Platforms | Linux process group, macOS process group |
| Runtimes | Python 3.13 and Python 3.14 |
| Compatibility kit | ACP Test Compatibility Kit plus adapter-specific regressions |

The fake-`agy` end-to-end suite is the primary deterministic oracle. The ACP Test Compatibility Kit is supplementary and does not replace adapter-specific MCP, redaction, or lifecycle coverage. An opt-in real `agy` smoke test verifies event shape and a fixed response hash without printing sensitive values.
