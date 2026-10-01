# agy-acp

## Purpose

`agy-acp` is a security-focused ACP v1 adapter for the Antigravity CLI (`agy`) on Linux and macOS. It supports Python 3.13 and 3.14. Complete ACP v1 compatibility is not yet claimed: client-provided stdio MCP handoff remains blocked by the absence of a safe per-process `agy` configuration interface.

## Installation

Install the current repository revision directly from GitHub:

```bash
python -m pip install "git+https://github.com/Kcrong/agy-acp.git"
```

A reproducible installation can pin any reviewed 40-character commit SHA:

```bash
python -m pip install "git+https://github.com/Kcrong/agy-acp.git@<commit-sha>"
```

Python 3.13 or 3.14 and an installed, authenticated `agy` executable are required.

## Implementation overview

The `agy-acp` console entry point serves bounded NDJSON over stdio, validates ACP requests before dispatch, and launches `agy` without a shell. Sessions use isolated process generations, opaque conversation resumption, ordered text streaming, bounded cancellation, and process-tree cleanup. Verified conditional support includes `session/close` and ordered absolute `additionalDirectories`; `session/load` remains unadvertised until complete ordered history replay is proven, and non-empty `mcpServers` requests fail closed.
