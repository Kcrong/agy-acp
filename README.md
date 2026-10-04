# agy-acp

[![CI status](https://github.com/Kcrong/agy-acp/actions/workflows/ci.yml/badge.svg)](https://github.com/Kcrong/agy-acp/actions/workflows/ci.yml) [![PyPI version](https://img.shields.io/pypi/v/agy-acp?include_prereleases)](https://pypi.org/project/agy-acp/) [![Python versions](https://img.shields.io/pypi/pyversions/agy-acp)](https://pypi.org/project/agy-acp/) [![License](https://img.shields.io/pypi/l/agy-acp)](./LICENSE)

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
pipx run --spec "agy-acp==X.Y.Z" agy-acp --help
```

If pipx selects an unsupported interpreter, pass an installed Python 3.13 or 3.14 executable with `--python`. A project virtual environment can instead use:

```bash
python -m pip install agy-acp
```

## Privacy and support

The adapter adds no telemetry, network listener, or credential store. Prompts, responses, MCP configuration, and account access remain subject to the installed `agy` CLI and its configured services. Treat the ACP client and its stdio MCP definitions as trusted local code: configured commands run as the current user and inherit the launch environment after adapter-reserved and Python import controls are removed. ACP clients receive standard session selectors for the models returned by `agy models` and for reasoning effort; changes apply to subsequent prompts, and restarting the adapter refreshes the account-specific model list. Client-provided stdio MCP servers, `session/resume`, `session/close`, and ordered absolute `additionalDirectories` are supported. HTTP, SSE, and ACP MCP transports are unsupported, and `session/load` remains unavailable because `agy` does not provide complete ordered history replay.
