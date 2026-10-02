# agy-acp

## Purpose

`agy-acp` is a security-focused ACP v1 adapter for the Antigravity CLI (`agy`). It exposes an authenticated local `agy` installation to ACP clients over standard input and output. The project passes a pinned revision of the experimental official ACP TCK, but that tool does not certify complete protocol coverage.

## Requirements

- Linux or macOS
- Python 3.13 or 3.14
- An installed and authenticated `agy` executable

Windows is outside the current support scope.

## Installation

Until the first PyPI release, install the current reviewed source from GitHub:

```bash
python -m pip install "git+https://github.com/Kcrong/agy-acp.git"
```

A reproducible installation can pin a reviewed 40-character commit SHA:

```bash
python -m pip install "git+https://github.com/Kcrong/agy-acp.git@<commit-sha>"
```

After a release is published on PyPI, install that release with:

```bash
python -m pip install agy-acp
```

## Privacy and support

The adapter adds no telemetry, network listener, or credential store. Prompts, responses, MCP configuration, and account access remain subject to the installed `agy` CLI and its configured services. Client-provided stdio MCP servers, `session/resume`, `session/close`, and ordered absolute `additionalDirectories` are supported. HTTP, SSE, and ACP MCP transports are unsupported, and `session/load` remains unavailable because `agy` does not provide complete ordered history replay.
