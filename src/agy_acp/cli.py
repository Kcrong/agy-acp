from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from collections.abc import Sequence

from acp import stdio_streams

from agy_acp import __version__
from agy_acp.agent import AgentConfig, AgyAgent
from agy_acp.executable import AgyCommand, resolve_executable
from agy_acp.models import discover_models
from agy_acp.protocol import AcpStdioServer

_DEFAULT_MAX_LINE_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_IN_FLIGHT = 16


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except (OverflowError, ValueError):
        raise argparse.ArgumentTypeError("value must be positive and finite") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return parsed


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except (OverflowError, ValueError):
        raise argparse.ArgumentTypeError("value must be positive") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agy-acp",
        description="ACP v1 adapter for the Antigravity CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--agy-path",
        default=os.environ.get("AGY_ACP_AGY_PATH", "agy"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--prompt-timeout",
        type=positive_float,
        default=30 * 60.0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-line-bytes",
        type=positive_integer,
        default=_DEFAULT_MAX_LINE_BYTES,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-in-flight",
        type=positive_integer,
        default=_DEFAULT_MAX_IN_FLIGHT,
        help=argparse.SUPPRESS,
    )
    return parser


async def serve(arguments: argparse.Namespace) -> None:
    executable = resolve_executable(arguments.agy_path)
    command = AgyCommand(executable)
    models = await discover_models(command)
    reader, writer = await stdio_streams(limit=arguments.max_line_bytes + 1)
    server: AcpStdioServer | None = None

    async def send_update(session_id: str, update: dict[str, object]) -> None:
        if server is None:
            raise RuntimeError("ACP server is not initialized")
        await server.send_session_update(session_id, update)

    agent = AgyAgent(
        AgentConfig(
            command=command,
            models=models,
            max_line_bytes=arguments.max_line_bytes,
            prompt_timeout=arguments.prompt_timeout,
        ),
        send_update,
    )
    server = AcpStdioServer(
        agent,
        reader,
        writer,
        max_line_bytes=arguments.max_line_bytes,
        max_in_flight=arguments.max_in_flight,
    )
    await server.serve()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        asyncio.run(serve(arguments))
    except KeyboardInterrupt:
        return 130
    except Exception:
        print("agy-acp: server failed", file=sys.stderr)
        return 1
    return 0
