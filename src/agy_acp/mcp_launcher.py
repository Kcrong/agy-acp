from __future__ import annotations

import os
import sys

from agy_acp.errors import InvalidMcpConfigError
from agy_acp.mcp import (
    MCP_ENV_PREFIX,
    MCP_SCRUBBED_ENVIRONMENT,
    decode_launcher_spec,
)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        return 127
    slot = arguments[0]
    if not slot or "\x00" in slot:
        return 127
    environment = dict(os.environ)
    raw = environment.get(MCP_ENV_PREFIX + slot)
    for name in list(environment):
        if name.startswith(MCP_ENV_PREFIX) or name in MCP_SCRUBBED_ENVIRONMENT:
            del environment[name]
    if raw is None:
        return 127
    try:
        spec = decode_launcher_spec(raw)
    except InvalidMcpConfigError:
        return 127
    for name, value in spec.env:
        environment[name] = value
    try:
        os.execve(
            spec.command,
            [spec.command, *spec.args],
            environment,
        )
    except OSError:
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
