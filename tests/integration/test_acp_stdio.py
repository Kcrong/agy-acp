from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, cast

import pytest

FIXTURE = Path(__file__).parents[1] / "fixtures" / "fake_agy.py"
MCP_FIXTURE = Path(__file__).parents[1] / "fixtures" / "fake_mcp_server.py"


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
    mcp_temp_parent: Path | None = None,
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
    environment = None
    if mcp_temp_parent is not None:
        environment = dict(os.environ)
        environment["TMPDIR"] = str(mcp_temp_parent)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
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
    assert "result" in response, response
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
async def test_stdio_rejects_unknown_method_and_accepts_stdio_mcp(tmp_path: Path) -> None:
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
    assert (await receive(process))["result"] == {"sessionId": "fake-session"}
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "type": "sse",
                        "name": "unsupported",
                        "url": "https://invalid.example/mcp",
                        "headers": [],
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
        "session-descendant",
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
    started = list(marker_root.glob("descendant-started-*"))
    assert len(started) == 1

    assert process.stdin is not None
    process.stdin.close()
    await process.stdin.wait_closed()
    async with asyncio.timeout(8):
        assert await process.wait() == 0
    await asyncio.sleep(1.3)
    assert not list(marker_root.glob("descendant-survived-*"))
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


@pytest.mark.asyncio
async def test_stdio_close_active_session_and_reject_duplicate(tmp_path: Path) -> None:
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
            "id": 4,
            "method": "session/close",
            "params": {"sessionId": session_id},
        },
    )

    terminal = [await receive(process), await receive(process)]
    by_id = {response["id"]: response for response in terminal}
    assert by_id[3]["result"] == {"stopReason": "cancelled"}
    assert by_id[4]["result"] == {}
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "session/close",
            "params": {"sessionId": session_id},
        },
    )
    assert (await receive(process))["error"] == {
        "code": -32015,
        "message": "Session not found",
    }
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_rejects_relative_additional_directory(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "normal")
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
                "additionalDirectories": ["credential-sentinel"],
            },
        },
    )
    response = await receive(process)
    assert response["error"] == {"code": -32602, "message": "Invalid params"}
    assert "sentinel" not in json.dumps(response)
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_request_cancelled_close_finishes_cleanup(tmp_path: Path) -> None:
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
    close_request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "session/close",
            "params": {"sessionId": session_id},
        },
        separators=(",", ":"),
    ).encode()
    cancel_request = b'{"jsonrpc":"2.0","method":"$/cancel_request","params":{"requestId":4}}'
    await send_bytes(process, close_request + b"\n" + cancel_request + b"\n")

    terminal = [await receive(process), await receive(process)]
    by_id = {response["id"]: response for response in terminal}
    assert by_id[3]["result"] == {"stopReason": "cancelled"}
    assert by_id[4]["error"] == {"code": -32800, "message": "Request cancelled"}
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "session/close",
            "params": {"sessionId": session_id},
        },
    )
    assert (await receive(process))["error"] == {
        "code": -32015,
        "message": "Session not found",
    }
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_rejects_nul_session_ids_without_echo(tmp_path: Path) -> None:
    process = await start_server(tmp_path, "normal")
    await initialize(process)
    bad_session_id = "credential\x00sentinel"
    for request_id, method, extra in (
        (2, "session/prompt", {"prompt": [{"type": "text", "text": "hello"}]}),
        (3, "session/close", {}),
    ):
        await send(
            process,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": {"sessionId": bad_session_id, **extra},
            },
        )
        response = await receive(process)
        assert response["error"] == {"code": -32602, "message": "Invalid params"}
        assert "sentinel" not in json.dumps(response)

    await send(
        process,
        {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": bad_session_id},
        },
    )
    await send(
        process,
        {"jsonrpc": "2.0", "id": 4, "method": "unknown", "params": {}},
    )
    assert await receive(process) == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32601, "message": "Method not found"},
    }
    await stop_server(process)


@pytest.mark.asyncio
async def test_stdio_disconnect_cleans_active_mcp_generation_and_descendant(
    tmp_path: Path,
) -> None:
    marker_root = tmp_path / "mcp-markers"
    mcp_temp_parent = tmp_path / "mcp-temp"
    marker_root.mkdir()
    mcp_temp_parent.mkdir()
    marker = marker_root / "server.json"
    survivor = marker_root / "server-survived"
    secret = "credential-sentinel"
    process = await start_server(
        tmp_path,
        "mcp-hang",
        marker_root=marker_root,
        mcp_temp_parent=mcp_temp_parent,
    )
    await initialize(process)
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "server",
                        "command": str(Path(sys.executable).resolve()),
                        "args": [
                            "-u",
                            str(MCP_FIXTURE),
                            str(marker),
                            "TOKEN",
                            hashlib.sha256(secret.encode()).hexdigest(),
                            "OTHER_TOKEN",
                            hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest(),
                            str(survivor),
                        ],
                        "env": [{"name": "TOKEN", "value": secret}],
                    }
                ],
            },
        },
    )
    session_id = str((await receive(process))["result"]["sessionId"])
    await send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "start"}],
            },
        },
    )
    assert (await receive(process))["method"] == "session/update"
    assert marker.exists()
    assert list(mcp_temp_parent.glob("agy-acp-mcp-owner-*/generation-*"))

    assert process.stdin is not None
    process.stdin.close()
    await process.stdin.wait_closed()
    async with asyncio.timeout(8):
        assert await process.wait() == 0
    await asyncio.sleep(1.3)

    assert not survivor.exists()
    assert not list(mcp_temp_parent.iterdir())
    assert process.stderr is not None
    assert await process.stderr.read() == b""
