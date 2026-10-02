from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import pytest

from agy_acp import __version__
from agy_acp.agent import AgentConfig, AgyAgent
from agy_acp.executable import AgyCommand
from agy_acp.protocol import AcpStdioServer


class BlockingWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.drain_started = asyncio.Event()
        self.release_drain = asyncio.Event()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        self.drain_started.set()
        await self.release_drain.wait()

    def close(self) -> None:
        self.closed = True
        self.release_drain.set()

    async def wait_closed(self) -> None:
        return None


async def make_agent() -> AgyAgent:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    return AgyAgent(
        AgentConfig(command=AgyCommand(Path("/usr/bin/false"))),
        send_update,
    )


@pytest.mark.asyncio
async def test_completed_result_wins_cancellation_during_blocked_output() -> None:
    reader = asyncio.StreamReader()
    writer = BlockingWriter()
    server = AcpStdioServer(
        await make_agent(),
        reader,
        cast(asyncio.StreamWriter, writer),
        max_line_bytes=4096,
    )
    serving = asyncio.create_task(server.serve())
    reader.feed_data(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            },
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    async with asyncio.timeout(1):
        await writer.drain_started.wait()
    reader.feed_data(b'{"jsonrpc":"2.0","method":"$/cancel_request","params":{"requestId":1}}\n')
    await asyncio.sleep(0)
    writer.release_drain.set()
    await asyncio.sleep(0)
    reader.feed_eof()
    async with asyncio.timeout(1):
        await serving

    records = [json.loads(line) for line in writer.data.splitlines()]
    assert records == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": False,
                    "promptCapabilities": {
                        "image": False,
                        "audio": False,
                        "embeddedContext": False,
                    },
                    "mcpCapabilities": {"http": False, "sse": False},
                    "sessionCapabilities": {
                        "additionalDirectories": {},
                        "resume": {},
                        "close": {},
                    },
                    "auth": {},
                },
                "authMethods": [],
                "agentInfo": {"name": "agy-acp", "version": __version__},
            },
        }
    ]
    assert writer.closed


@pytest.mark.asyncio
async def test_protocol_requires_positive_request_bound() -> None:
    reader = asyncio.StreamReader()
    writer = cast(asyncio.StreamWriter, BlockingWriter())
    with pytest.raises(ValueError, match="max_in_flight must be a positive integer"):
        AcpStdioServer(
            await make_agent(),
            reader,
            writer,
            max_line_bytes=4096,
            max_in_flight=0,
        )


@pytest.mark.asyncio
async def test_completed_error_wins_cancellation_during_blocked_output() -> None:
    reader = asyncio.StreamReader()
    writer = BlockingWriter()
    server = AcpStdioServer(
        await make_agent(),
        reader,
        cast(asyncio.StreamWriter, writer),
        max_line_bytes=4096,
    )
    serving = asyncio.create_task(server.serve())
    reader.feed_data(
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"invalid"}}\n'
    )
    async with asyncio.timeout(1):
        await writer.drain_started.wait()
    reader.feed_data(b'{"jsonrpc":"2.0","method":"$/cancel_request","params":{"requestId":1}}\n')
    await asyncio.sleep(0)
    writer.release_drain.set()
    await asyncio.sleep(0)
    reader.feed_eof()
    async with asyncio.timeout(1):
        await serving

    assert [json.loads(line) for line in writer.data.splitlines()] == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32602, "message": "Invalid params"},
        }
    ]
    assert writer.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [0, -1, True])
async def test_protocol_requires_positive_line_bound(invalid: object) -> None:
    reader = asyncio.StreamReader()
    writer = cast(asyncio.StreamWriter, BlockingWriter())
    with pytest.raises(ValueError, match="max_line_bytes must be a positive integer"):
        AcpStdioServer(
            await make_agent(),
            reader,
            writer,
            max_line_bytes=cast(int, invalid),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [0, -1, True, float("nan"), float("inf"), 10**1000, "credential-sentinel"],
)
async def test_protocol_requires_positive_write_timeout_without_echo(
    invalid: object,
) -> None:
    reader = asyncio.StreamReader()
    writer = cast(asyncio.StreamWriter, BlockingWriter())
    with pytest.raises(ValueError, match="write_timeout must be positive and finite") as raised:
        AcpStdioServer(
            await make_agent(),
            reader,
            writer,
            max_line_bytes=4096,
            write_timeout=cast(float, invalid),
        )
    assert "sentinel" not in str(raised.value)


class FailingWriter(BlockingWriter):
    def write(self, data: bytes) -> None:
        del data
        raise OSError


@pytest.mark.asyncio
async def test_write_timeout_never_emits_two_terminal_records() -> None:
    reader = asyncio.StreamReader()
    writer = BlockingWriter()
    server = AcpStdioServer(
        await make_agent(),
        reader,
        cast(asyncio.StreamWriter, writer),
        max_line_bytes=4096,
        write_timeout=0.01,
    )
    serving = asyncio.create_task(server.serve())
    reader.feed_data(
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}\n'
    )

    async with asyncio.timeout(1):
        await serving
    records = [json.loads(line) for line in writer.data.splitlines()]
    assert len(records) == 1
    assert records[0]["id"] == 1
    assert "result" in records[0]
    assert writer.closed


@pytest.mark.asyncio
async def test_synchronous_write_failure_closes_without_second_response() -> None:
    reader = asyncio.StreamReader()
    writer = FailingWriter()
    server = AcpStdioServer(
        await make_agent(),
        reader,
        cast(asyncio.StreamWriter, writer),
        max_line_bytes=4096,
    )
    serving = asyncio.create_task(server.serve())
    reader.feed_data(
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}\n'
    )

    async with asyncio.timeout(1):
        await serving
    assert writer.data == b""
    assert writer.closed


@pytest.mark.asyncio
async def test_protocol_rejects_unsafe_request_buffer_product() -> None:
    reader = asyncio.StreamReader()
    writer = cast(asyncio.StreamWriter, BlockingWriter())
    with pytest.raises(ValueError, match="combined request buffer limits are unsafe"):
        AcpStdioServer(
            await make_agent(),
            reader,
            writer,
            max_line_bytes=4 * 1024 * 1024,
            max_in_flight=17,
        )


@pytest.mark.asyncio
async def test_cancelling_idle_server_leaves_no_protocol_tasks() -> None:
    reader = asyncio.StreamReader()
    writer = BlockingWriter()
    server = AcpStdioServer(
        await make_agent(),
        reader,
        cast(asyncio.StreamWriter, writer),
        max_line_bytes=4096,
    )
    serving = asyncio.create_task(server.serve())
    await asyncio.sleep(0)

    serving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await serving
    await asyncio.sleep(0)

    current = asyncio.current_task()
    leaked = [
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not current and not task.done() and task.get_name().startswith("agy-acp.")
    ]
    assert leaked == []
    assert writer.closed
