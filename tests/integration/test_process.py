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
    BackendWriteError,
    ProtocolEncodingError,
)
from agy_acp.events import AgyResultEvent, AgyStepUpdateEvent
from agy_acp.executable import AgyCommand
from agy_acp.process import AgyProcess

FIXTURE = Path(__file__).parents[1] / "fixtures" / "fake_agy.py"


def config(
    tmp_path: Path,
    mode: str,
    *extra: str,
    max_line_bytes: int = 4096,
    max_pending_events: int = 8,
    write_timeout: float = 0.2,
    init_timeout: float = 2,
) -> AgyProcessConfig:
    return AgyProcessConfig(
        command=AgyCommand(
            Path(sys.executable).resolve(),
            ("-u", str(FIXTURE), mode, *extra),
        ),
        cwd=tmp_path,
        max_line_bytes=max_line_bytes,
        max_stderr_bytes=64,
        max_pending_events=max_pending_events,
        init_timeout=init_timeout,
        write_timeout=write_timeout,
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


@pytest.mark.asyncio
async def test_cancel_terminates_descendant_tree(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "descendant", str(tmp_path)))
    started = tmp_path / "descendant-started"
    survived = tmp_path / "descendant-survived"
    assert started.exists()

    await process.cancel()
    await asyncio.sleep(1.4)

    assert process.closed
    assert not survived.exists()


@pytest.mark.asyncio
async def test_idle_root_exit_reaps_descendants_in_background(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "idle-exit-descendant", str(tmp_path)))
    started = tmp_path / "descendant-started"
    survived = tmp_path / "descendant-survived"
    assert started.exists()

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


@pytest.mark.asyncio
async def test_clean_stdout_eof_with_live_root_is_protocol_error(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "close-stdout"))

    with pytest.raises(BackendProtocolError, match="invalid output"):
        await process.receive(timeout=1)

    assert process.closed


@pytest.mark.asyncio
async def test_event_queue_is_bounded_and_released_on_cancel(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "flood", max_pending_events=8))
    await send_prompt(process)
    await asyncio.sleep(0.1)

    assert process.pending_events == 8
    await process.cancel()
    assert process.pending_events == 0
    assert process.closed


@pytest.mark.asyncio
async def test_write_timeout_closes_nonreading_backend(tmp_path: Path) -> None:
    process = await AgyProcess.launch(
        config(
            tmp_path,
            "no-read",
            max_line_bytes=8 * 1024 * 1024,
            write_timeout=0.05,
        )
    )

    with pytest.raises(BackendWriteError, match="input failed"):
        await process.send({"payload": "x" * (4 * 1024 * 1024)})

    assert process.closed


@pytest.mark.asyncio
async def test_cancelled_backpressured_send_closes_backend(tmp_path: Path) -> None:
    process = await AgyProcess.launch(
        config(
            tmp_path,
            "no-read",
            max_line_bytes=8 * 1024 * 1024,
            write_timeout=5,
        )
    )
    sending = asyncio.create_task(process.send({"payload": "x" * (4 * 1024 * 1024)}))
    await asyncio.sleep(0.05)
    assert not sending.done()

    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending

    assert process.closed


@pytest.mark.asyncio
async def test_close_is_bounded_during_backpressured_send(tmp_path: Path) -> None:
    process = await AgyProcess.launch(
        config(
            tmp_path,
            "no-read",
            max_line_bytes=8 * 1024 * 1024,
            write_timeout=5,
        )
    )
    sending = asyncio.create_task(process.send({"payload": "x" * (4 * 1024 * 1024)}))
    await asyncio.sleep(0.05)

    started = time.monotonic()
    await process.close()
    assert time.monotonic() - started < 1
    with pytest.raises((BackendExitedError, BackendWriteError)):
        await sending
    assert process.closed


@pytest.mark.asyncio
async def test_repeated_caller_cancellation_waits_for_shutdown(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "ignore-term"))
    await send_prompt(process)
    assert isinstance(await process.receive(timeout=2), AgyStepUpdateEvent)

    cleanup = asyncio.create_task(process.cancel())
    await asyncio.sleep(0.02)
    cleanup.cancel()
    await asyncio.sleep(0.02)
    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup

    assert process.closed
    await asyncio.sleep(0)
    assert not _active_process_tasks()


def _active_process_tasks() -> list[asyncio.Task[object]]:
    current = asyncio.current_task()
    return [
        task
        for task in asyncio.all_tasks()
        if task is not current and not task.done() and task.get_name().startswith("agy-acp.")
    ]


@pytest.mark.asyncio
async def test_parser_fault_closed_is_a_task_quiescence_barrier(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "malformed"))
    await send_prompt(process)

    with pytest.raises(BackendProtocolError):
        await process.receive(timeout=2)

    assert process.closed
    await asyncio.sleep(0)
    assert not _active_process_tasks()


@pytest.mark.asyncio
async def test_fault_cleanup_exception_is_observed_without_payload(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "normal"))

    async def fail_cleanup() -> None:
        raise RuntimeError("credential-sentinel")

    cleanup = asyncio.create_task(fail_cleanup())
    cleanup.add_done_callback(process._observe_fault_cleanup)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert process.fault_cleanup_failed
    assert cleanup.done()
    await process.close()


@pytest.mark.asyncio
async def test_launch_exposes_process_before_initialization_wait(tmp_path: Path) -> None:
    started: list[AgyProcess] = []

    with pytest.raises(BackendTimeoutError, match="timed out"):
        await AgyProcess.launch(
            config(tmp_path, "slow-init", init_timeout=0.05),
            on_started=started.append,
        )

    assert len(started) == 1
    assert started[0].closed
