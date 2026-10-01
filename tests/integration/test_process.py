from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from agy_acp.config import AgyProcessConfig
from agy_acp.errors import (
    BackendExitedError,
    BackendProtocolError,
    BackendTimeoutError,
    ProtocolEncodingError,
)
from agy_acp.events import AgyResultEvent, AgyStepUpdateEvent
from agy_acp.executable import AgyCommand
from agy_acp.process import AgyProcess

FIXTURE = Path(__file__).parents[1] / "fixtures" / "fake_agy.py"


def config(tmp_path: Path, mode: str, *extra: str) -> AgyProcessConfig:
    return AgyProcessConfig(
        command=AgyCommand(
            Path(sys.executable).resolve(),
            ("-u", str(FIXTURE), mode, *extra),
        ),
        cwd=tmp_path,
        max_line_bytes=4096,
        max_stderr_bytes=64,
        init_timeout=2,
        cancel_grace=0.15,
        kill_grace=2,
    )


async def send_prompt(process: AgyProcess) -> None:
    await process.send(
        {
            "event": "user",
            "message": {"role": "user", "content": "hello"},
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "split"])
async def test_normal_and_split_streams_preserve_event_order(
    tmp_path: Path,
    mode: str,
) -> None:
    process = await AgyProcess.launch(config(tmp_path, mode))
    try:
        assert process.init_event.conversation_id == "fake-session"
        await send_prompt(process)
        update = await process.receive(timeout=2)
        result = await process.receive(timeout=2)
        assert isinstance(update, AgyStepUpdateEvent)
        assert update.text_delta == "fake-response"
        assert isinstance(result, AgyResultEvent)
        assert result.response == "fake-response"
    finally:
        await process.close()
    assert process.closed
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_unknown_events_are_discarded(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "unknown"))
    try:
        await send_prompt(process)
        assert isinstance(await process.receive(timeout=2), AgyStepUpdateEvent)
        assert isinstance(await process.receive(timeout=2), AgyResultEvent)
    finally:
        await process.close()


@pytest.mark.asyncio
async def test_malformed_backend_output_fails_and_closes(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "malformed"))
    await send_prompt(process)

    with pytest.raises(BackendProtocolError, match="invalid output"):
        await process.receive(timeout=2)

    assert process.closed


@pytest.mark.asyncio
async def test_exit_before_init_is_typed(tmp_path: Path) -> None:
    with pytest.raises(BackendExitedError, match="before initialization"):
        await AgyProcess.launch(config(tmp_path, "no-init"))


@pytest.mark.asyncio
async def test_exit_during_turn_is_typed_and_closes(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "early-exit"))
    await send_prompt(process)

    with pytest.raises(BackendExitedError, match="unexpectedly"):
        await process.receive(timeout=2)

    assert process.closed


@pytest.mark.asyncio
async def test_receive_timeout_cancels_process(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "hang"))
    await send_prompt(process)
    assert isinstance(await process.receive(timeout=2), AgyStepUpdateEvent)

    with pytest.raises(BackendTimeoutError, match="timed out"):
        await process.receive(timeout=0.05)

    assert process.closed


@pytest.mark.asyncio
async def test_close_gracefully_ends_idle_process(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "normal"))
    await process.close()

    assert process.closed
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_ignored_graceful_signal_is_hard_killed(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "ignore-term"))
    await send_prompt(process)
    assert isinstance(await process.receive(timeout=2), AgyStepUpdateEvent)

    started = time.monotonic()
    await process.cancel()

    assert process.closed
    assert time.monotonic() - started < 2
    assert process.returncode not in (None, 0)


async def wait_for_path(path: Path) -> None:
    async with asyncio.timeout(2):
        while not path.exists():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_cancel_terminates_descendant_tree(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "descendant", str(tmp_path)))
    started = tmp_path / "descendant-started"
    survived = tmp_path / "descendant-survived"
    await wait_for_path(started)

    await process.cancel()
    await asyncio.sleep(1.4)

    assert process.closed
    assert not survived.exists()


@pytest.mark.asyncio
async def test_idle_root_exit_reaps_descendants_in_background(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "idle-exit-descendant", str(tmp_path)))
    started = tmp_path / "descendant-started"
    survived = tmp_path / "descendant-survived"
    await wait_for_path(started)

    async with asyncio.timeout(2):
        while not process.closed:
            await asyncio.sleep(0.01)
    await asyncio.sleep(1.4)

    assert process.returncode == 0
    assert not survived.exists()


@pytest.mark.asyncio
async def test_stderr_is_bounded_without_exposing_content(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "stderr"))
    try:
        assert process.stderr_retained_bytes == 64
        assert process.stderr_truncated
        assert not hasattr(process, "stderr_text")
    finally:
        await process.close()


@pytest.mark.asyncio
async def test_send_applies_output_line_limit(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "normal"))
    try:
        with pytest.raises(ProtocolEncodingError, match="line limit"):
            await process.send({"payload": "x" * 5000})
    finally:
        await process.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, True, float("nan"), float("inf"), 10**1000, "1"])
async def test_receive_timeout_must_be_positive_and_finite(
    tmp_path: Path,
    timeout: object,
) -> None:
    process = await AgyProcess.launch(config(tmp_path, "normal"))
    try:
        with pytest.raises(ValueError, match="positive and finite"):
            await process.receive(timeout=timeout)  # type: ignore[arg-type]
        assert not process.closed
    finally:
        await process.close()


@pytest.mark.asyncio
async def test_cancelling_cancel_caller_still_completes_cleanup(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "ignore-term"))
    await send_prompt(process)
    assert isinstance(await process.receive(timeout=2), AgyStepUpdateEvent)

    cleanup = asyncio.create_task(process.cancel())
    await asyncio.sleep(0.02)
    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup

    assert process.closed
    assert process.returncode not in (None, 0)
