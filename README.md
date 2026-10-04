# agy-acp

[![CI status](https://github.com/Kcrong/agy-acp/actions/workflows/ci.yml/badge.svg)](https://github.com/Kcrong/agy-acp/actions/workflows/ci.yml) [![PyPI version](https://img.shields.io/pypi/v/agy-acp?include_prereleases)](https://pypi.org/project/agy-acp/) [![Python versions](https://img.shields.io/pypi/pyversions/agy-acp)](https://pypi.org/project/agy-acp/) [![License](https://img.shields.io/pypi/l/agy-acp)](./LICENSE)

## Purpose

`agy-acp` is a security-focused ACP v1 adapter for the Antigravity CLI (`agy`). It lets an ACP client use an authenticated local `agy` installation over standard input and output. It is a protocol adapter, not an interactive chat command or a network server.

The project passes a pinned revision of the experimental official ACP TCK, but that tool does not certify complete protocol coverage.

## Requirements

- Linux or macOS
- Python 3.13 or 3.14
- An installed and authenticated `agy` executable available on `PATH`

Windows is outside the current support scope. Verify the backend before installing the adapter:

```bash
agy --version
agy models
```

`agy-acp` discovers the account-available model list with `agy models` at startup, so that command must succeed in the same environment used by the ACP client.

## Installation

For an isolated command-line installation, use [pipx](https://pipx.pypa.io/stable/):

```bash
pipx install agy-acp
agy-acp --version
```

Upgrade an existing installation with `pipx upgrade agy-acp`. `pipx install` creates the isolated environment during installation. It does not install or authenticate the required `agy` backend.

Without a permanent installation, `pipx run` creates and caches a temporary environment on first use:

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

## Run the adapter

Check the installed command and its public options:

```bash
agy-acp --version
agy-acp --help
```

Starting the command without an option launches the ACP stdio server:

```bash
agy-acp
```

The process then waits for newline-delimited ACP JSON-RPC on standard input and writes protocol messages to standard output. No prompt or terminal UI is displayed; normally an ACP client launches and manages this process. Pressing Ctrl+C exits a direct invocation.

## Configure an ACP client

ACP client configuration formats differ, but the command and argument pair for an installed adapter is:

```json
{
  "command": "agy-acp",
  "args": []
}
```

To let pipx provide the adapter without a permanent installation, use:

```json
{
  "command": "pipx",
  "args": ["run", "agy-acp"]
}
```

Place the object under the agent entry required by the client, then restart or reconnect the client. Ensure its launch environment can resolve both `agy-acp` (or `pipx`) and `agy` on `PATH`. The client supplies an existing absolute working directory when creating or resuming a session.

## Options

### Command-line options

| Option | Behavior |
| --- | --- |
| `-h`, `--help` | Show command usage and exit. |
| `--version` | Print the installed `agy-acp` version and exit. |

The public CLI intentionally has no `--model` or `--effort` flags. Those values are negotiated per session through standard ACP configuration options.

### ACP session options

Compatible ACP clients receive these model and reasoning effort selectors and session fields:

| Option or field | Behavior |
| --- | --- |
| `model` | Offers `default`, followed by the account-specific models returned by `agy models`. |
| `effort` | Offers `default` for the agy-selected model and unsuffixed concrete models. For models ending in `-low`, `-medium`, `-high`, or `-max`, it offers the discovered sibling variants from that family. |
| `cwd` | Required existing absolute directory for a new or resumed session. |
| `additionalDirectories` | Optional ordered absolute directories passed to `agy` as literal arguments. |
| `mcpServers` | `session/new` requires a list (use `[]` when none); `session/resume` may omit it. Entries define stdio MCP servers with command, arguments, and environment values. |

Model and effort changes apply from the next prompt. An unsuffixed concrete model keeps `effort` at `default`. Returning `model` to `default` also resets `effort` to `default`. Restarting the adapter refreshes the cached model catalog.

## Supported ACP behavior

- Create, prompt, cancel, resume, and close independent sessions.
- Stream ordered text updates and accept text or resource-link prompt blocks.
- Resume an `agy` conversation identity without replaying prior history.
- Run client-provided stdio MCP servers and preserve ordered additional directories.
- Reject unsupported or malformed methods and values with sanitized protocol errors.

`session/load`, HTTP/SSE/ACP MCP transports, image/audio prompt blocks, and experimental ACP v2 features are not supported or advertised.

## Troubleshooting

- **`agy-acp: server failed`:** run `agy --version` and `agy models` in the same launch environment. The adapter intentionally keeps stderr errors generic and does not expose backend output.
- **`agy` cannot be found:** add the directory containing `agy` to the ACP client's inherited `PATH`; a terminal and a GUI-launched client may inherit different environments.
- **No output after running `agy-acp`:** this is expected for a stdio server waiting for an ACP client, not an interactive prompt.
- **Models or effort levels look stale:** restart the adapter so it reruns bounded model discovery.
- **pipx uses the wrong Python:** reinstall or run with pipx's `--python` option pointing to Python 3.13 or 3.14.

Report reproducible defects through the [GitHub issue tracker](https://github.com/Kcrong/agy-acp/issues). Security reports follow the [security policy](https://github.com/Kcrong/agy-acp/security/policy).

## Privacy and support

The adapter adds no telemetry, network listener, or credential store. Prompts, responses, MCP configuration, and account access remain subject to the installed `agy` CLI and its configured services.

Treat the ACP client and its stdio MCP definitions as trusted local code: configured commands run as the current user and inherit the launch environment after adapter-reserved and Python import controls are removed. The installed `agy` process is also inside the trusted boundary.
