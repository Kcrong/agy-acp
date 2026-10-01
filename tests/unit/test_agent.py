from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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
) -> AgentConfig:
    return AgentConfig(
        command=AgyCommand(
            Path(sys.executable).resolve(),
            ("-u", str(FIXTURE), mode),
        ),
        max_line_bytes=4096,
        max_stderr_bytes=64,
        max_pending_events=8,
        max_sessions=max_sessions,
        init_timeout=init_timeout,
        write_timeout=1,
        prompt_timeout=prompt_timeout,
        cancel_grace=0.1,
        kill_grace=1,
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
        "sessionCapabilities": {},
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


@pytest.mark.parametrize("mode", ["pre-result", "duplicate-result", "late-update"])
@pytest.mark.asyncio
async def test_prompt_rejects_events_outside_one_terminal_generation(
    tmp_path: Path,
    mode: str,
) -> None:
    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config(tmp_path, mode), send_update)
    session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    if mode == "pre-result":
        await asyncio.sleep(0.05)
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
async def test_delayed_duplicate_cannot_cross_prompt_generation(tmp_path: Path) -> None:
    updates: list[dict[str, object]] = []

    async def send_update(_session_id: str, update: dict[str, object]) -> None:
        updates.append(update)

    agent = AgyAgent(agent_config(tmp_path, "delayed-duplicate"), send_update)
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
