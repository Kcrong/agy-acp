from __future__ import annotations

import asyncio
import json
import sys

import pytest


@pytest.mark.asyncio
async def test_bootstrap_forwards_small_prompt_without_waiting_for_eof() -> None:
    target = "import sys; print(sys.stdin.readline().strip(), flush=True)"
    command = [sys.executable, "-u", "-c", target]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agy_acp._windows_bootstrap",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    control = json.dumps(command, separators=(",", ":")).encode() + b"\n"
    process.stdin.write(control + b"small-prompt\n")
    await process.stdin.drain()

    async with asyncio.timeout(2):
        assert await process.stdout.readline() == b"small-prompt\n"

    process.stdin.close()
    await process.stdin.wait_closed()
    async with asyncio.timeout(2):
        assert await process.wait() == 0
    assert await process.stderr.read() == b""


@pytest.mark.asyncio
async def test_bootstrap_rejects_invalid_control_without_stderr() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agy_acp._windows_bootstrap",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    process.stdin.write(b"{}\n")
    await process.stdin.drain()
    process.stdin.close()
    await process.stdin.wait_closed()

    async with asyncio.timeout(2):
        assert await process.wait() == 2
    assert await process.stdout.read() == b""
    assert await process.stderr.read() == b""
