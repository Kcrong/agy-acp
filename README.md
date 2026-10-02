# agy-acp

## Purpose

`agy-acp` is a security-focused ACP v1 adapter for the Antigravity CLI (`agy`). It exposes an authenticated local `agy` installation to ACP clients over standard input and output. The project passes a pinned revision of the experimental official ACP TCK, but that tool does not certify complete protocol coverage.

## Requirements

- Linux or macOS
- Python 3.13 or 3.14
- An installed and authenticated `agy` executable

Windows is outside the current support scope.

## Installation

For an isolated command-line installation, use [pipx](https://pipx.pypa.io/stable/):

```bash
pipx install agy-acp
agy-acp --version
```

pipx downloads `agy-acp` into an isolated environment on first use. It does not install or authenticate the required `agy` backend.

Configure the ACP client to launch `agy-acp`. Without a permanent app installation, configure it to launch `pipx run agy-acp`; verify resolution with:

```bash
pipx run agy-acp --help
```

Pin an exact release when reproducibility matters:

```bash
pipx run --spec "agy-acp==0.1.0a2" agy-acp --help
```

If pipx selects an unsupported interpreter, pass an installed Python 3.13 or 3.14 executable with `--python`. A project virtual environment can instead use:

```bash
python -m pip install agy-acp
```

## Privacy and support

The adapter adds no telemetry, network listener, or credential store. Prompts, responses, MCP configuration, and account access remain subject to the installed `agy` CLI and its configured services. Client-provided stdio MCP servers, `session/resume`, `session/close`, and ordered absolute `additionalDirectories` are supported. HTTP, SSE, and ACP MCP transports are unsupported, and `session/load` remains unavailable because `agy` does not provide complete ordered history replay.
