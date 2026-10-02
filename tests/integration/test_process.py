from __future__ import annotations

import asyncio
import signal
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

import agy_acp.process as process_module
from agy_acp.config import AgyProcessConfig
from agy_acp.errors import (
    BackendExitedError,
    BackendProtocolError,
    BackendShutdownError,
    BackendStartError,
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
    kill_grace: float = 2,
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
        kill_grace=kill_grace,
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
    started = list(tmp_path.glob("descendant-started-*"))
    assert len(started) == 1

    await process.cancel()
    await asyncio.sleep(1.4)

    assert process.closed
    assert not list(tmp_path.glob("descendant-survived-*"))


@pytest.mark.asyncio
async def test_idle_root_exit_reaps_descendants_in_background(tmp_path: Path) -> None:
    process = await AgyProcess.launch(config(tmp_path, "idle-exit-descendant", str(tmp_path)))
    started = list(tmp_path.glob("descendant-started-*"))
    assert len(started) == 1

    async with asyncio.timeout(2):
        while not process.closed:
            await asyncio.sleep(0.01)
    await asyncio.sleep(1.4)

    assert process.returncode == 0
    assert not list(tmp_path.glob("descendant-survived-*"))


@pytest.mark.asyncio
async def test_process_group_cleanup_waits_through_darwin_zombie_eperm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = await AgyProcess.launch(config(tmp_path, "normal", kill_grace=0.2))
    probes: list[BaseException | None] = [
        PermissionError(),
        PermissionError(),
        ProcessLookupError(),
    ]
    signals: list[int] = []

    def scripted_kill_group(pid: int, requested_signal: int) -> None:
        assert pid == process.pid
        signals.append(requested_signal)
        if requested_signal != 0:
            return
        outcome = probes.pop(0)
        if outcome is not None:
            raise outcome

    with monkeypatch.context() as patch:
        patch.setattr(process_module, "_kill_process_group", scripted_kill_group)
        await asyncio.gather(process.close(), process.close())

    assert signals.count(signal.SIGKILL) == 1
    assert signals.count(0) == 3
    assert process.closed
    assert probes == []


@pytest.mark.asyncio
async def test_persistent_process_group_eperm_fails_closed_without_resignal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback_calls: list[None] = []
    process = await AgyProcess.launch(
        replace(
            config(tmp_path, "normal", kill_grace=0.03),
            shutdown_callback=lambda: callback_calls.append(None),
        )
    )
    signals: list[int] = []

    def deny_process_group(pid: int, requested_signal: int) -> None:
        assert pid == process.pid
        signals.append(requested_signal)
        raise PermissionError

    with monkeypatch.context() as patch:
        patch.setattr(process_module, "_kill_process_group", deny_process_group)
        with pytest.raises(BackendShutdownError, match="shutdown failed"):
            await process.close()
        attempts = len(signals)
        with pytest.raises(BackendShutdownError, match="shutdown failed"):
            await process.close()

    assert attempts > 1
    assert len(signals) == attempts
    assert signals.count(signal.SIGKILL) == 1
    assert not process.closed
    assert callback_calls == []
    await asyncio.sleep(0)
    assert not _active_process_tasks()


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
async def test_spawn_filters_ambient_reserved_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_ACP_MCP_SPEC_STALE", "credential-sentinel")
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "hostile-home"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    process = await AgyProcess.launch(config(tmp_path, "record-env", str(tmp_path)))
    try:
        assert (tmp_path / "agy-environment.json").read_text(encoding="utf-8") == (
            '{"stale_spec_absent":true,"python_controls_absent":true}'
        )
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
            max_pending_events=1,
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
            max_pending_events=1,
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
            max_pending_events=1,
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
    process._fault_cleanup = cleanup
    cleanup.add_done_callback(process._observe_fault_cleanup)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert process.fault_cleanup_failed
    assert cleanup.done()
    assert not process.fault_cleanup_active
    await process.close()
    await process.close()
    assert process.closed


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


@pytest.mark.asyncio
async def test_shutdown_callback_failure_is_fixed_retryable_and_once_only(
    tmp_path: Path,
) -> None:
    attempts: list[None] = []

    def cleanup() -> None:
        attempts.append(None)
        if len(attempts) == 1:
            raise RuntimeError("credential-sentinel")

    def cleanup_attempts() -> int:
        return len(attempts)

    process_config = config(tmp_path, "normal")
    process_config = AgyProcessConfig(
        command=process_config.command,
        cwd=process_config.cwd,
        additional_directories=process_config.additional_directories,
        conversation_id=process_config.conversation_id,
        shutdown_callback=cleanup,
        max_line_bytes=process_config.max_line_bytes,
        max_stderr_bytes=process_config.max_stderr_bytes,
        max_pending_events=process_config.max_pending_events,
        init_timeout=process_config.init_timeout,
        write_timeout=process_config.write_timeout,
        cancel_grace=process_config.cancel_grace,
        kill_grace=process_config.kill_grace,
    )
    process = await AgyProcess.launch(process_config)

    def process_is_closed() -> bool:
        return process.closed

    with pytest.raises(BackendShutdownError, match="shutdown failed") as raised:
        await process.close()
    assert "credential-sentinel" not in str(raised.value)
    assert not process_is_closed()
    assert cleanup_attempts() == 1

    await process.close()
    assert process_is_closed()
    await process.close()
    assert cleanup_attempts() == 2


@pytest.mark.asyncio
async def test_cancelled_spawn_failure_is_bounded_and_diagnostic_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    cleanup_calls: list[None] = []
    diagnostics: list[dict[str, object]] = []

    async def fail_after_release(
        _config: AgyProcessConfig,
        _argv: tuple[str, ...],
    ) -> asyncio.subprocess.Process:
        entered.set()
        await release.wait()
        raise OSError("credential-sentinel")

    process_config = replace(
        config(tmp_path, "normal", init_timeout=0.5),
        shutdown_callback=lambda: cleanup_calls.append(None),
    )
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: diagnostics.append(context))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AgyProcess, "_spawn", staticmethod(fail_after_release))
            launching = asyncio.create_task(AgyProcess.launch(process_config))
            await entered.wait()
            launching.cancel()
            launching.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                async with asyncio.timeout(1):
                    await launching
        await asyncio.sleep(0)
        assert cleanup_calls == [None]
        assert diagnostics == []
        assert not _active_process_tasks()
    finally:
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_cancelled_never_finishing_spawn_stops_at_launch_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    cleanup_calls: list[None] = []

    async def never_finish(
        _config: AgyProcessConfig,
        _argv: tuple[str, ...],
    ) -> asyncio.subprocess.Process:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError

    process_config = replace(
        config(tmp_path, "normal", init_timeout=0.05),
        shutdown_callback=lambda: cleanup_calls.append(None),
    )
    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "_spawn", staticmethod(never_finish))
        launching = asyncio.create_task(AgyProcess.launch(process_config))
        await entered.wait()
        launching.cancel()
        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout(1):
                await launching

    assert cleanup_calls == [None]
    assert not _active_process_tasks()


@pytest.mark.asyncio
async def test_pre_spawn_failure_runs_shutdown_callback(tmp_path: Path) -> None:
    attempts = 0

    def cleanup() -> None:
        nonlocal attempts
        attempts += 1

    process_config = config(tmp_path, "normal")
    process_config = AgyProcessConfig(
        command=process_config.command,
        cwd=process_config.cwd,
        additional_directories=(Path("/safe\x00tail"),),
        shutdown_callback=cleanup,
        max_line_bytes=process_config.max_line_bytes,
        max_stderr_bytes=process_config.max_stderr_bytes,
        max_pending_events=process_config.max_pending_events,
        init_timeout=process_config.init_timeout,
        write_timeout=process_config.write_timeout,
        cancel_grace=process_config.cancel_grace,
        kill_grace=process_config.kill_grace,
    )

    with pytest.raises(BackendStartError, match="could not start"):
        await AgyProcess.launch(process_config)
    assert attempts == 1
