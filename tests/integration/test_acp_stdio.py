from __future__ import annotations

import asyncio
import json
import shlex
import sys
from pathlib import Path
from typing import Any, cast

import pytest

FIXTURE = Path(__file__).parents[1] / "fixtures" / "fake_agy.py"


def create_wrapper(tmp_path: Path, mode: str, marker_root: Path | None = None) -> Path:
    wrapper = tmp_path / "agy-wrapper"
    marker_argument = "" if marker_root is None else " " + shlex.quote(str(marker_root))
    wrapper.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " -u "
        + shlex.quote(str(FIXTURE))
        + " "
        + shlex.quote(mode)
        + marker_argument
        + ' "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


async def start_server(
    tmp_path: Path,
    mode: str,
    *,
    max_line_bytes: int | None = None,
    max_in_flight: int | None = None,
    marker_root: Path | None = None,
) -> asyncio.subprocess.Process:
    command = [
        sys.executable,
        "-m",
        "agy_acp",
        "--agy-path",
        str(create_wrapper(tmp_path, mode, marker_root)),
        "--prompt-timeout",
        "2",
    ]
    if max_line_bytes is not None:
        command.extend(("--max-line-bytes", str(max_line_bytes)))
    if max_in_flight is not None:
        command.extend(("--max-in-flight", str(max_in_flight)))
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    return process


async def send(process: asyncio.subprocess.Process, message: dict[str, object]) -> None:
    await send_bytes(
        process,
        json.dumps(message, separators=(",", ":")).encode() + b"\n",
    )


async def send_bytes(process: asyncio.subprocess.Process, payload: bytes) -> None:
    assert process.stdin is not None
    process.stdin.write(payload)
    await process.stdin.drain()


async def receive(process: asyncio.subprocess.Process) -> dict[str, Any]:
    assert process.stdout is not None
    async with asyncio.timeout(3):
        line = await process.stdout.readline()
    assert line
    return cast(dict[str, Any], json.loads(line))


async def initialize(process: asyncio.subprocess.Process) -> None:
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1, "clientCapabilities": {}},
        },
    )
    response = await receive(process)
    assert response["id"] == 1
    assert response["result"]["protocolVersion"] == 1


async def new_session(process: asyncio.subprocess.Process, cwd: Path) -> str:
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": str(cwd), "mcpServers": []},
        },
    )
    response = await receive(process)
    assert response["id"] == 2
    return str(response["result"]["sessionId"])


async def stop_server(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
        await process.stdin.wait_closed()
    async with asyncio.timeout(3):
        assert await process.wait() == 0
    assert process.stderr is not None
    assert await process.stderr.read() == b""


@pytest.mark.asyncio
async def test_stdio_initialize_new_prompt_update_and_disconnect(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "normal")
    await initialize(process)
    session_id = await new_session(process, tmp_path)
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "hello"}],
            },
        },
    )

    update = await receive(process)
    response = await receive(process)
    assert update["method"] == "session/update"
    assert update["params"]["sessionId"] == session_id
    assert update["params"]["update"]["content"]["text"] == "fake-response"
    assert response == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {"stopReason": "end_turn"},
    }
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_session_cancel_completes_prompt_as_cancelled(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "hang")
    await initialize(process)
    session_id = await new_session(process, tmp_path)
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "hello"}],
            },
        },
    )
    assert (await receive(process))["method"] == "session/update"
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": session_id},
        },
    )

    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {"stopReason": "cancelled"},
    }
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_rejects_unknown_method_and_nonempty_mcp(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "normal")
    await initialize(process)
    await send(
        process,
        {"jsonrpc": "2.0", "id": 8, "method": "unknown", "params": {}},
    )
    assert (await receive(process))["error"] == {
        "code": -32601,
        "message": "Method not found",
    }
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "server",
                        "command": "/usr/bin/false",
                        "args": [],
                        "env": [],
                    }
                ],
            },
        },
    )
    assert (await receive(process))["error"] == {
        "code": -32602,
        "message": "Invalid params",
    }
    await stop_server(process)


@pytest.mark.parametrize(
    ("payload", "expected_code", "expected_message"),
    [
        (b"{broken}\n", -32700, "Parse error"),
        (b"[]\n", -32600, "Invalid request"),
        (b'{"value":"' + b"x" * 600 + b'"}\n', -32600, "Invalid request"),
    ],
)
@pytest.mark.asyncio
async def test_stdio_rejects_malformed_or_oversized_input(
    tmp_path: Path,
    payload: bytes,
    expected_code: int,
    expected_message: str,
) -> None:
    process = await start_server(tmp_path, "normal", max_line_bytes=512)
    await send_bytes(process, payload)

    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": expected_code, "message": expected_message},
    }
    await send(
        process,
        {"jsonrpc": "2.0", "id": 9, "method": "unknown", "params": {}},
    )
    assert (await receive(process))["error"] == {
        "code": -32601,
        "message": "Method not found",
    }
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_rejects_extra_envelope_members_without_echo(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "normal")
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1, "clientCapabilities": {}},
            "credential-sentinel": "must-not-survive",
        },
    )

    response = await receive(process)
    assert response == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Invalid request"},
    }
    assert "sentinel" not in json.dumps(response)
    await initialize(process)
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [],
                "credential-sentinel": "must-not-survive",
            },
        },
    )
    response = await receive(process)
    assert response["error"] == {"code": -32602, "message": "Invalid params"}
    assert "sentinel" not in json.dumps(response)
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_duplicate_request_id_is_rejected(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "hang")
    await initialize(process)
    session_id = await new_session(process, tmp_path)
    prompt = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "session/prompt",
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "hello"}],
        },
    }
    await send(process, prompt)
    assert (await receive(process))["method"] == "session/update"
    await send(process, prompt)

    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32600, "message": "Invalid request"},
    }
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": session_id},
        },
    )
    assert (await receive(process))["result"] == {"stopReason": "cancelled"}
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_request_level_cancellation_returns_fixed_error(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "hang")
    await initialize(process)
    session_id = await new_session(process, tmp_path)
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "hello"}],
            },
        },
    )
    assert (await receive(process))["method"] == "session/update"
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "method": "$/cancel_request",
            "params": {"requestId": 3},
        },
    )

    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "second"}],
            },
        },
    )
    assert (await receive(process))["method"] == "session/update"
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": session_id},
        },
    )
    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": 4,
        "result": {"stopReason": "cancelled"},
    }
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_disconnect_terminates_backend_process_group(tmp_path: Path) -> None:
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    process = await start_server(
        tmp_path,
        "descendant",
        marker_root=marker_root,
    )
    await initialize(process)
    session_id = await new_session(process, tmp_path)
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "hello"}],
            },
        },
    )
    assert (await receive(process))["method"] == "session/update"
    started = marker_root / "descendant-started"
    async with asyncio.timeout(2):
        while not started.exists():
            await asyncio.sleep(0.01)

    assert process.stdin is not None
    process.stdin.close()
    await process.stdin.wait_closed()
    async with asyncio.timeout(8):
        assert await process.wait() == 0
    await asyncio.sleep(1.3)
    assert not (marker_root / "descendant-survived").exists()
    assert process.stderr is not None
    assert await process.stderr.read() == b""


@pytest.mark.asyncio
async def test_stdio_bounds_concurrent_request_tasks(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "hang", max_in_flight=1)
    await initialize(process)
    session_id = await new_session(process, tmp_path)
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "hello"}],
            },
        },
    )
    assert (await receive(process))["method"] == "session/update"
    await send(
        process,
        {"jsonrpc": "2.0", "id": 4, "method": "unknown", "params": {}},
    )
    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32600, "message": "Invalid request"},
    }
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": session_id},
        },
    )
    assert (await receive(process))["result"] == {"stopReason": "cancelled"}
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_rejects_salvaged_initialize_fields_without_echo(
    tmp_path: Path,
) -> None:
    process = await start_server(tmp_path, "normal")
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": "credential-sentinel"},
            },
        },
    )
    response = await receive(process)
    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "Invalid params"},
    }
    assert "sentinel" not in json.dumps(response)
    await initialize(process)
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_keeps_valid_record_before_coalesced_parse_error(
    tmp_path: Path,
) -> None:
    process = await start_server(tmp_path, "normal")
    initialize_record = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1, "clientCapabilities": {}},
        },
        separators=(",", ":"),
    ).encode()
    await send_bytes(process, initialize_record + b"\n{broken}\n")

    responses = [await receive(process), await receive(process)]
    assert any(response.get("id") == 1 and "result" in response for response in responses)
    assert any(
        response.get("error") == {"code": -32700, "message": "Parse error"}
        for response in responses
    )
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_rejects_protocol_version_without_common_v1(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "normal")
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 0, "clientCapabilities": {}},
        },
    )
    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "Invalid params"},
    }
    await initialize(process)
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_coalesced_request_cancel_returns_fixed_error(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "slow-init")
    await initialize(process)
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        },
        separators=(",", ":"),
    ).encode()
    cancel = b'{"jsonrpc":"2.0","method":"$/cancel_request","params":{"requestId":2}}'
    await send_bytes(process, request + b"\n" + cancel + b"\n")

    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    await stop_server(process)
