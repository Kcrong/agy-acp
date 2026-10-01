from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from agy_acp.agent import AgentConfig, AgyAgent, serialize_prompt
from agy_acp.errors import BackendShutdownError
from agy_acp.executable import AgyCommand
from agy_acp.process import AgyProcess
from agy_acp.protocol import AcpRequestError

FIXTURE = Path(__file__).parents[1] / "fixtures" / "fake_agy.py"


def agent_config(
    tmp_path: Path,
    mode: str,
    *,
    prompt_timeout: float = 2,
    init_timeout: float = 2,
    max_sessions: int = 16,
    max_pending_events: int = 8,
    kill_grace: float = 1,
    marker_root: Path | None = None,
) -> AgentConfig:
    prefix_args: tuple[str, ...] = ("-u", str(FIXTURE), mode)
    if marker_root is not None:
        prefix_args += (str(marker_root),)
    return AgentConfig(
        command=AgyCommand(
            Path(sys.executable).resolve(),
            prefix_args,
        ),
        max_line_bytes=4096,
        max_stderr_bytes=64,
        max_pending_events=max_pending_events,
        max_sessions=max_sessions,
        init_timeout=init_timeout,
        write_timeout=1,
        prompt_timeout=prompt_timeout,
        cancel_grace=0.1,
        kill_grace=kill_grace,
    )


def test_serialize_prompt_preserves_supported_block_order() -> None:
    assert (
        serialize_prompt(
            [
                {"type": "text", "text": "first"},
                {"type": "text", "text": ""},
                {
                    "type": "resource_link",
                    "name": "design",
                    "uri": "file:///workspace/design.md",
                },
                {"type": "text", "text": "last"},
            ]
        )
        == "first\n\nResource: design (file:///workspace/design.md)\n\nlast"
    )


@pytest.mark.parametrize(
    "blocks",
    [
        [],
        [{"type": "text", "text": ""}],
        [{"type": "image", "data": "secret"}],
        [{"type": "text", "text": 1}],
        [{"type": "text", "text": "safe", "annotations": "invalid"}],
        [{"type": "text", "text": "safe", "secret": "must-not-survive"}],
        [{"type": "resource_link", "name": "x"}],
    ],
)
def test_serialize_prompt_rejects_invalid_or_empty_input_without_echo(
    blocks: list[dict[str, object]],
) -> None:
    with pytest.raises(AcpRequestError, match="Invalid params") as raised:
        serialize_prompt(blocks)
    assert "secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_initialize_advertises_only_baseline_capabilities(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    response = await agent.initialize(protocol_version=65535)
    payload = response.model_dump(mode="json", by_alias=True, exclude_none=True)

    assert payload["protocolVersion"] == 1
    assert payload["agentCapabilities"] == {
        "loadSession": False,
        "promptCapabilities": {
            "image": False,
            "audio": False,
            "embeddedContext": False,
        },
        "mcpCapabilities": {"http": False, "sse": False, "acp": False},
        "sessionCapabilities": {
            "additionalDirectories": {},
            "close": {},
        },
        "auth": {},
    }


@pytest.mark.asyncio
async def test_new_session_and_prompt_stream_one_message(tmp_path: Path) -> None:
    updates: list[tuple[str, dict[str, object]]] = []

    async def send_update(session_id: str, update: dict[str, object]) -> None:
        updates.append((session_id, update))

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    response = await agent.prompt(
        session_id=session.session_id,
        prompt=[{"type": "text", "text": "hello"}],
    )
    resumed = await agent.prompt(
        session_id=session.session_id,
        prompt=[{"type": "text", "text": "second"}],
    )

    assert response.stop_reason == "end_turn"
    assert resumed.stop_reason == "end_turn"
    assert len(updates) == 2
    assert {update[0] for update in updates} == {session.session_id}
    assert all(update[1]["sessionUpdate"] == "agent_message_chunk" for update in updates)
    assert all(
        update[1]["content"] == {"type": "text", "text": "fake-response"} for update in updates
    )
    assert updates[0][1]["messageId"] != updates[1][1]["messageId"]
    await agent.close()


@pytest.mark.asyncio
async def test_nonempty_mcp_is_a_disclosed_release_blocker(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    with pytest.raises(AcpRequestError, match="Invalid params"):
        await agent.new_session(
            cwd=str(tmp_path),
            mcp_servers=[
                {
                    "name": "server",
                    "command": "/usr/bin/false",
                    "args": [],
                    "env": [],
                }
            ],
        )


@pytest.mark.asyncio
async def test_session_cancel_returns_cancelled_prompt(tmp_path: Path) -> None:
    update_arrived = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()

    agent = AgyAgent(agent_config(tmp_path, "hang"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()

    await agent.cancel(session.session_id)
    response = await prompting

    assert response.stop_reason == "cancelled"
    update_arrived.clear()
    resumed = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "second"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()
    await agent.cancel(session.session_id)
    assert (await resumed).stop_reason == "cancelled"
    await agent.close()


@pytest.mark.asyncio
async def test_unknown_session_is_fixed_and_payload_free(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    secret = "credential-sentinel"
    with pytest.raises(AcpRequestError, match="Session not found") as raised:
        await agent.prompt(
            session_id=secret,
            prompt=[{"type": "text", "text": "hello"}],
        )
    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    ("mode", "expected_texts"),
    [
        ("suffix", ["fake-", "response"]),
        ("no-delta", ["fake-response"]),
        ("empty-delta", ["fake-response"]),
    ],
)
@pytest.mark.asyncio
async def test_prompt_emits_only_the_unstreamed_final_suffix(
    tmp_path: Path,
    mode: str,
    expected_texts: list[str],
) -> None:
    updates: list[dict[str, object]] = []

    async def send_update(_session_id: str, update: dict[str, object]) -> None:
        updates.append(update)

    agent = AgyAgent(agent_config(tmp_path, mode), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    response = await agent.prompt(
        session_id=session.session_id,
        prompt=[{"type": "text", "text": "hello"}],
    )

    assert response.stop_reason == "end_turn"
    assert [update["content"] for update in updates] == [
        {"type": "text", "text": text} for text in expected_texts
    ]
    assert len({update["messageId"] for update in updates}) == 1
    await agent.close()


@pytest.mark.parametrize("mode", ["conflict", "mismatch", "result-mismatch"])
@pytest.mark.asyncio
async def test_prompt_rejects_conflicting_or_mismatched_backend_results(
    tmp_path: Path,
    mode: str,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, mode), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Backend unavailable") as raised:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    assert "different-response" not in str(raised.value)
    assert "other-session" not in str(raised.value)
    await agent.close()


@pytest.mark.asyncio
async def test_cancel_wins_before_an_unprocessed_terminal_result(tmp_path: Path) -> None:
    update_arrived = asyncio.Event()
    release_update = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()
        await release_update.wait()

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()
    cancelling = asyncio.create_task(agent.cancel(session.session_id))
    await asyncio.sleep(0)
    release_update.set()

    assert (await prompting).stop_reason == "cancelled"
    await cancelling
    await agent.close()


@pytest.mark.asyncio
async def test_prompt_timeout_is_fixed_and_payload_free(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(tmp_path, "hang", prompt_timeout=0.05),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Prompt timed out") as raised:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "credential-sentinel"}],
        )
    assert raised.value.code == -32012
    assert "sentinel" not in str(raised.value)
    with pytest.raises(AcpRequestError, match="Prompt timed out") as retried:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "second"}],
        )
    assert retried.value.code == -32012
    await agent.close()


@pytest.mark.asyncio
async def test_cancelled_prompt_waits_for_process_cleanup_barrier(tmp_path: Path) -> None:
    update_arrived = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()

    agent = AgyAgent(agent_config(tmp_path, "ignore-term"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()
    started = asyncio.get_running_loop().time()
    cancelling = asyncio.create_task(agent.cancel(session.session_id))

    assert (await prompting).stop_reason == "cancelled"
    assert asyncio.get_running_loop().time() - started >= 0.08
    await cancelling
    await agent.close()


@pytest.mark.asyncio
async def test_initialization_timeout_has_dedicated_fixed_error(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(tmp_path, "slow-init", init_timeout=0.05),
        send_update,
    )
    with pytest.raises(AcpRequestError, match="Initialization timed out") as raised:
        await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    assert raised.value.code == -32011
    await agent.close()


@pytest.mark.asyncio
async def test_oversized_serialized_prompt_is_invalid_params(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Invalid params") as raised:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "x" * 5000}],
        )
    assert raised.value.code == -32602
    await agent.close()


@pytest.mark.parametrize("max_sessions", [0, -1, True])
def test_agent_config_requires_positive_session_capacity(
    tmp_path: Path,
    max_sessions: int,
) -> None:
    with pytest.raises(ValueError, match="max_sessions must be a positive integer"):
        agent_config(tmp_path, "normal", max_sessions=max_sessions)


@pytest.mark.asyncio
async def test_active_session_capacity_is_bounded(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(tmp_path, "normal", max_sessions=1),
        send_update,
    )
    await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Session capacity exceeded") as raised:
        await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    assert raised.value.code == -32014
    await agent.close()


@pytest.mark.asyncio
async def test_starting_session_reserves_and_releases_capacity(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(
            tmp_path,
            "slow-init",
            init_timeout=0.05,
            max_sessions=1,
        ),
        send_update,
    )
    starting = asyncio.create_task(agent.new_session(cwd=str(tmp_path), mcp_servers=[]))
    await asyncio.sleep(0.01)
    with pytest.raises(AcpRequestError, match="Session capacity exceeded"):
        await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Initialization timed out"):
        await starting
    with pytest.raises(AcpRequestError, match="Initialization timed out") as retried:
        await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    assert retried.value.code == -32011
    await agent.close()


@pytest.mark.parametrize("mode", ["duplicate-result", "late-update"])
@pytest.mark.asyncio
async def test_prompt_rejects_events_outside_one_terminal_generation(
    tmp_path: Path,
    mode: str,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, mode), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Backend unavailable"):
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    with pytest.raises(AcpRequestError, match="Backend unavailable"):
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "second"}],
        )
    await agent.close()


@pytest.mark.asyncio
async def test_backend_cancelled_result_maps_to_cancelled(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, "backend-canceled"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])

    response = await agent.prompt(
        session_id=session.session_id,
        prompt=[{"type": "text", "text": "hello"}],
    )
    resumed = await agent.prompt(
        session_id=session.session_id,
        prompt=[{"type": "text", "text": "second"}],
    )
    assert response.stop_reason == "cancelled"
    assert resumed.stop_reason == "cancelled"
    await agent.close()


@pytest.mark.asyncio
async def test_slow_update_uses_prompt_deadline_and_restarts_generation(
    tmp_path: Path,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        await asyncio.sleep(1)

    agent = AgyAgent(
        agent_config(tmp_path, "normal", prompt_timeout=0.05),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Prompt timed out") as raised:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    assert raised.value.code == -32012
    with pytest.raises(AcpRequestError, match="Prompt timed out") as retried:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "second"}],
        )
    assert retried.value.code == -32012
    await agent.close()


@pytest.mark.asyncio
async def test_session_cancel_wins_over_failed_update_delivery(tmp_path: Path) -> None:
    update_arrived = asyncio.Event()
    release_update = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()
        await release_update.wait()
        raise RuntimeError("credential-sentinel")

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()
    cancelling = asyncio.create_task(agent.cancel(session.session_id))
    await asyncio.sleep(0)
    release_update.set()

    assert (await prompting).stop_reason == "cancelled"
    await cancelling
    await agent.close()


@pytest.mark.parametrize(
    "mode",
    [
        "backend-error",
        "backend-interrupted",
        "backend-invalid",
        "backend-waiting",
        "backend-running",
    ],
)
@pytest.mark.asyncio
async def test_unsolicited_nonterminal_or_failed_results_fail_closed(
    tmp_path: Path,
    mode: str,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, mode), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Backend unavailable") as raised:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    assert raised.value.code == -32010
    with pytest.raises(AcpRequestError, match="Backend unavailable"):
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "second"}],
        )
    await agent.close()


@pytest.mark.asyncio
async def test_delayed_duplicate_fails_each_prompt_generation(tmp_path: Path) -> None:
    updates: list[dict[str, object]] = []

    async def send_update(_session_id: str, update: dict[str, object]) -> None:
        updates.append(update)

    agent = AgyAgent(agent_config(tmp_path, "delayed-duplicate"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])

    for text in ("first", "second"):
        with pytest.raises(AcpRequestError, match="Backend unavailable") as raised:
            await agent.prompt(
                session_id=session.session_id,
                prompt=[{"type": "text", "text": text}],
            )
        assert raised.value.code == -32010

    assert [update["content"] for update in updates] == [
        {"type": "text", "text": "fake-response"},
        {"type": "text", "text": "fake-response"},
    ]
    await agent.close()


@pytest.mark.asyncio
async def test_failed_request_cancel_cleanup_quarantines_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_arrived = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()

    agent = AgyAgent(agent_config(tmp_path, "hang"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()

    async def fail_cancel(_process: AgyProcess) -> None:
        raise BackendShutdownError

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "cancel", fail_cancel)
        prompting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await prompting
        with pytest.raises(AcpRequestError, match="Session not found"):
            await agent.prompt(
                session_id=session.session_id,
                prompt=[{"type": "text", "text": "second"}],
            )

    await agent.close()


@pytest.mark.asyncio
async def test_nul_backend_conversation_id_is_rejected(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, "nul-conversation"), send_update)
    with pytest.raises(AcpRequestError, match="Backend unavailable") as raised:
        await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    assert raised.value.code == -32010
    await agent.close()


@pytest.mark.asyncio
async def test_resumed_generation_init_timeout_uses_dedicated_error(
    tmp_path: Path,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(tmp_path, "restart-slow-init", init_timeout=1),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Initialization timed out") as raised:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    assert raised.value.code == -32011
    await agent.close()


@pytest.mark.asyncio
async def test_initial_close_failure_is_quarantined_for_connection_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(tmp_path, "normal", max_sessions=1),
        send_update,
    )

    async def fail_retire(_process: AgyProcess) -> None:
        raise BackendShutdownError

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "retire", fail_retire)
        with pytest.raises(AcpRequestError, match="Backend unavailable"):
            await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
        with pytest.raises(AcpRequestError, match="Session capacity exceeded") as full:
            await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
        assert full.value.code == -32014

    cancel_calls = 0
    original_cancel = AgyProcess.cancel

    async def track_cancel(process: AgyProcess) -> None:
        nonlocal cancel_calls
        cancel_calls += 1
        await original_cancel(process)

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "cancel", track_cancel)
        await agent.close()
    assert cancel_calls == 1


@pytest.mark.asyncio
async def test_backend_operation_cleanup_failure_quarantines_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])

    async def fail_receive(_process: AgyProcess, *, timeout: float) -> object:
        del timeout
        raise BackendShutdownError

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "receive", fail_receive)
        with pytest.raises(AcpRequestError, match="Backend unavailable"):
            await agent.prompt(
                session_id=session.session_id,
                prompt=[{"type": "text", "text": "hello"}],
            )
        with pytest.raises(AcpRequestError, match="Session not found"):
            await agent.prompt(
                session_id=session.session_id,
                prompt=[{"type": "text", "text": "second"}],
            )

    await agent.close()


@pytest.mark.parametrize(
    "directories",
    [["relative"], [""], ["/safe\x00tail"]],
)
@pytest.mark.asyncio
async def test_additional_directories_require_absolute_safe_paths(
    tmp_path: Path,
    directories: list[str],
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, "normal"), send_update)
    with pytest.raises(AcpRequestError, match="Invalid params"):
        await agent.new_session(
            cwd=str(tmp_path),
            mcp_servers=[],
            additional_directories=directories,
        )
    await agent.close()


@pytest.mark.asyncio
async def test_additional_directories_preserve_order_across_generations(
    tmp_path: Path,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    expected = [str(first), str(second), str(first)]
    agent = AgyAgent(
        agent_config(tmp_path, "record-args", marker_root=marker_root),
        send_update,
    )
    session = await agent.new_session(
        cwd=str(tmp_path),
        mcp_servers=[],
        additional_directories=expected,
    )
    assert (
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    ).stop_reason == "end_turn"

    records = [
        json.loads(line)
        for line in (marker_root / "argv.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records == [expected, expected]
    await agent.close_session(session.session_id)
    await agent.close()


@pytest.mark.asyncio
async def test_close_idle_session_releases_capacity_and_is_idempotent_error(
    tmp_path: Path,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(tmp_path, "normal", max_sessions=1),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    response = await agent.close_session(session.session_id)
    assert response.model_dump(mode="json", by_alias=True, exclude_none=True) == {}
    with pytest.raises(AcpRequestError, match="Session not found"):
        await agent.close_session(session.session_id)
    with pytest.raises(AcpRequestError, match="Session not found"):
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "closed"}],
        )
    replacement = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    await agent.close_session(replacement.session_id)
    await agent.close()


@pytest.mark.asyncio
async def test_close_active_session_cancels_prompt_before_returning(
    tmp_path: Path,
) -> None:
    update_arrived = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()

    agent = AgyAgent(agent_config(tmp_path, "hang"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()

    closed = await agent.close_session(session.session_id)
    assert closed.model_dump(mode="json", by_alias=True, exclude_none=True) == {}
    assert (await prompting).stop_reason == "cancelled"
    with pytest.raises(AcpRequestError, match="Session not found"):
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "closed"}],
        )
    await agent.close()


@pytest.mark.asyncio
async def test_concurrent_sessions_keep_unique_process_generations(
    tmp_path: Path,
) -> None:
    updates: list[str] = []

    async def send_update(session_id: str, _update: dict[str, object]) -> None:
        updates.append(session_id)

    first_cwd = tmp_path / "one"
    second_cwd = tmp_path / "two"
    first_cwd.mkdir()
    second_cwd.mkdir()
    agent = AgyAgent(
        agent_config(tmp_path, "unique", max_sessions=2),
        send_update,
    )
    first, second = await asyncio.gather(
        agent.new_session(cwd=str(first_cwd), mcp_servers=[]),
        agent.new_session(cwd=str(second_cwd), mcp_servers=[]),
    )
    assert first.session_id != second.session_id

    responses = await asyncio.gather(
        agent.prompt(
            session_id=first.session_id,
            prompt=[{"type": "text", "text": "first"}],
        ),
        agent.prompt(
            session_id=second.session_id,
            prompt=[{"type": "text", "text": "second"}],
        ),
    )
    assert [response.stop_reason for response in responses] == ["end_turn", "end_turn"]
    assert set(updates) == {first.session_id, second.session_id}
    await asyncio.gather(
        agent.close_session(first.session_id),
        agent.close_session(second.session_id),
    )
    await agent.close()


@pytest.mark.asyncio
async def test_close_cleanup_failure_quarantines_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_arrived = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()

    agent = AgyAgent(agent_config(tmp_path, "hang"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()

    async def fail_cancel(_process: AgyProcess) -> None:
        raise BackendShutdownError

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "cancel", fail_cancel)
        with pytest.raises(AcpRequestError, match="Backend unavailable"):
            await agent.close_session(session.session_id)
        with pytest.raises(AcpRequestError, match="Backend unavailable"):
            await prompting
        with pytest.raises(AcpRequestError, match="Session not found"):
            await agent.close_session(session.session_id)

    await agent.close()


@pytest.mark.asyncio
async def test_close_session_during_generation_launch(tmp_path: Path) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    agent = AgyAgent(
        agent_config(
            tmp_path,
            "restart-slow-init",
            init_timeout=2,
            marker_root=marker_root,
        ),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        while not (marker_root / "restart-started").exists():
            await asyncio.sleep(0.01)

    started = asyncio.get_running_loop().time()
    closed = await agent.close_session(session.session_id)
    assert asyncio.get_running_loop().time() - started < 1
    assert closed.model_dump(mode="json", by_alias=True, exclude_none=True) == {}
    assert (await prompting).stop_reason == "cancelled"
    with pytest.raises(AcpRequestError, match="Session not found"):
        await agent.close_session(session.session_id)
    await agent.close()


@pytest.mark.asyncio
async def test_close_launch_cleanup_failure_quarantines_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    agent = AgyAgent(
        agent_config(
            tmp_path,
            "restart-slow-init",
            init_timeout=1,
            marker_root=marker_root,
        ),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        while not (marker_root / "restart-started").exists():
            await asyncio.sleep(0.01)

    async def fail_cancel(_process: AgyProcess) -> None:
        raise BackendShutdownError

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "cancel", fail_cancel)
        with pytest.raises(AcpRequestError, match="Backend unavailable"):
            await agent.close_session(session.session_id)
        with pytest.raises(AcpRequestError, match="Backend unavailable"):
            await prompting
        with pytest.raises(AcpRequestError, match="Session not found"):
            await agent.close_session(session.session_id)

    await agent.close()


@pytest.mark.asyncio
async def test_connection_close_retries_orphan_from_cancelled_session_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    agent = AgyAgent(
        agent_config(
            tmp_path,
            "slow-init-marker",
            init_timeout=2,
            marker_root=marker_root,
        ),
        send_update,
    )
    starting = asyncio.create_task(agent.new_session(cwd=str(tmp_path), mcp_servers=[]))
    async with asyncio.timeout(2):
        while not (marker_root / "initial-started").exists():
            await asyncio.sleep(0.01)

    cancel_calls = 0
    original_cancel = AgyProcess.cancel

    async def fail_once(process: AgyProcess) -> None:
        nonlocal cancel_calls
        cancel_calls += 1
        if cancel_calls == 1:
            raise BackendShutdownError
        await original_cancel(process)

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "cancel", fail_once)
        await agent.close()

    with pytest.raises(AcpRequestError, match="Backend unavailable"):
        await starting
    assert cancel_calls == 2


@pytest.mark.asyncio
async def test_concurrent_connection_close_callers_share_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_arrived = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()

    agent = AgyAgent(agent_config(tmp_path, "hang"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()

    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    original_cancel = AgyProcess.cancel

    async def slow_cancel(process: AgyProcess) -> None:
        cleanup_started.set()
        await release_cleanup.wait()
        await original_cancel(process)

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "cancel", slow_cancel)
        first = asyncio.create_task(agent.close())
        async with asyncio.timeout(2):
            await cleanup_started.wait()
        second = asyncio.create_task(agent.close())
        await asyncio.sleep(0)
        assert not first.done()
        assert not second.done()
        release_cleanup.set()
        await asyncio.gather(first, second)

    assert (await prompting).stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_connection_close_retries_retained_orphan_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_arrived = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()

    agent = AgyAgent(agent_config(tmp_path, "hang"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()

    cancel_calls = 0
    original_cancel = AgyProcess.cancel

    async def fail_twice(process: AgyProcess) -> None:
        nonlocal cancel_calls
        cancel_calls += 1
        if cancel_calls <= 2:
            raise BackendShutdownError
        await original_cancel(process)

    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "cancel", fail_twice)
        with pytest.raises(BackendShutdownError):
            await agent.close()
        with pytest.raises(AcpRequestError, match="Backend unavailable"):
            await prompting
        await agent.close()

    assert cancel_calls == 3


@pytest.mark.parametrize(
    "mode",
    ["result-error-exit", "result-sigterm", "result-sigkill", "result-malformed-tail"],
)
@pytest.mark.asyncio
async def test_success_result_with_failed_retirement_fails_closed(
    tmp_path: Path,
    mode: str,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, mode), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    with pytest.raises(AcpRequestError, match="Backend unavailable") as raised:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    assert raised.value.code == -32010
    await agent.close_session(session.session_id)
    await agent.close()


@pytest.mark.parametrize(
    "field_name",
    ["max_line_bytes", "max_stderr_bytes", "max_pending_events", "max_sessions"],
)
@pytest.mark.parametrize("invalid", [0, -1, True])
def test_agent_config_rejects_every_invalid_integer_limit(
    tmp_path: Path,
    field_name: str,
    invalid: object,
) -> None:
    base = agent_config(tmp_path, "normal")
    with pytest.raises(ValueError, match=rf"^{field_name} must be a positive integer$"):
        cast(Any, replace)(base, **{field_name: invalid})


@pytest.mark.parametrize(
    "field_name",
    ["init_timeout", "write_timeout", "prompt_timeout", "cancel_grace", "kill_grace"],
)
@pytest.mark.parametrize(
    "invalid",
    [0, -1, True, float("nan"), float("inf"), 10**1000, "credential-sentinel"],
)
def test_agent_config_rejects_every_invalid_duration_without_echo(
    tmp_path: Path,
    field_name: str,
    invalid: object,
) -> None:
    base = agent_config(tmp_path, "normal")
    with pytest.raises(ValueError, match=rf"^{field_name} must be positive and finite$") as raised:
        cast(Any, replace)(base, **{field_name: invalid})
    assert "sentinel" not in str(raised.value)


@pytest.mark.parametrize(
    "mode",
    [
        "pre-result",
        "duplicate-init",
        "malformed-after-init",
        "init-exit-error",
        "init-exit-signal",
        "nul-cwd",
    ],
)
@pytest.mark.asyncio
async def test_new_session_rejects_invalid_initial_retirement_and_releases_capacity(
    tmp_path: Path,
    mode: str,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(tmp_path, mode, max_sessions=1),
        send_update,
    )
    for _attempt in range(2):
        with pytest.raises(AcpRequestError, match="Backend unavailable") as raised:
            await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
        assert raised.value.code == -32010
    await agent.close()


@pytest.mark.asyncio
async def test_success_exit_zero_is_independent_of_slow_update(
    tmp_path: Path,
) -> None:
    updates: list[dict[str, object]] = []

    async def send_update(_session_id: str, update: dict[str, object]) -> None:
        await asyncio.sleep(0.05)
        updates.append(update)

    agent = AgyAgent(agent_config(tmp_path, "result-exit-zero"), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    first = await agent.prompt(
        session_id=session.session_id,
        prompt=[{"type": "text", "text": "first"}],
    )
    second = await agent.prompt(
        session_id=session.session_id,
        prompt=[{"type": "text", "text": "second"}],
    )

    assert first.stop_reason == "end_turn"
    assert second.stop_reason == "end_turn"
    assert [update["content"] for update in updates] == [
        {"type": "text", "text": "fake-response"},
        {"type": "text", "text": "fake-response"},
    ]
    await agent.close_session(session.session_id)
    await agent.close()


@pytest.mark.parametrize("operation", ["cancel", "close"])
@pytest.mark.asyncio
async def test_session_termination_interrupts_blocked_update_immediately(
    tmp_path: Path,
    operation: str,
) -> None:
    update_arrived = asyncio.Event()
    never_release = asyncio.Event()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        update_arrived.set()
        await never_release.wait()

    agent = AgyAgent(
        agent_config(tmp_path, "normal", prompt_timeout=2),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        await update_arrived.wait()
    started = asyncio.get_running_loop().time()

    if operation == "cancel":
        await agent.cancel(session.session_id)
    else:
        await agent.close_session(session.session_id)
    assert (await prompting).stop_reason == "cancelled"
    assert asyncio.get_running_loop().time() - started < 1
    if operation == "cancel":
        await agent.close_session(session.session_id)
    await agent.close()


@pytest.mark.asyncio
async def test_session_cancel_interrupts_generation_startup_immediately(
    tmp_path: Path,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    agent = AgyAgent(
        agent_config(
            tmp_path,
            "restart-slow-init",
            init_timeout=2,
            marker_root=marker_root,
        ),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "hello"}],
        )
    )
    async with asyncio.timeout(2):
        while not (marker_root / "restart-started").exists():
            await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()

    await agent.cancel(session.session_id)
    assert (await prompting).stop_reason == "cancelled"
    assert asyncio.get_running_loop().time() - started < 1
    await agent.close_session(session.session_id)
    await agent.close()


def test_agent_config_rejects_unsafe_global_event_buffer_product(tmp_path: Path) -> None:
    base = agent_config(tmp_path, "normal")
    with pytest.raises(ValueError, match="combined event buffer limits are unsafe"):
        replace(
            base,
            max_line_bytes=4 * 1024 * 1024,
            max_pending_events=4,
            max_sessions=16,
        )


@pytest.mark.asyncio
async def test_clean_exit_drains_saturated_queue_before_process_end(
    tmp_path: Path,
) -> None:
    updates: list[str] = []

    async def send_update(_session_id: str, update: dict[str, object]) -> None:
        await asyncio.sleep(0.2)
        content = cast(dict[str, object], update["content"])
        updates.append(cast(str, content["text"]))

    agent = AgyAgent(
        agent_config(
            tmp_path,
            "burst-exit-zero",
            max_pending_events=2,
            kill_grace=0.05,
            prompt_timeout=2,
        ),
        send_update,
    )
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])

    response = await agent.prompt(
        session_id=session.session_id,
        prompt=[{"type": "text", "text": "hello"}],
    )

    assert response.stop_reason == "end_turn"
    assert updates == ["x", "x", "x", "x"]
    await agent.close_session(session.session_id)
    await agent.close()
