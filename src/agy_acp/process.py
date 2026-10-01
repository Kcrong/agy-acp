from __future__ import annotations

import asyncio
import contextlib
import math
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agy_acp.config import AgyProcessConfig
from agy_acp.errors import (
    BackendExitedError,
    BackendProcessError,
    BackendProtocolError,
    BackendShutdownError,
    BackendStartError,
    BackendTimeoutError,
    BackendWriteError,
)
from agy_acp.events import AgyEvent, AgyInitEvent, AgyUnknownEvent, parse_agy_event
from agy_acp.executable import build_agy_argv
from agy_acp.ndjson import NdjsonParser
from agy_acp.transport import encode_json_line


@dataclass(frozen=True, slots=True)
class _ProcessEnd:
    returncode: int


class _ByteRing:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self._total = 0

    def append(self, chunk: bytes) -> None:
        self._total += len(chunk)
        if len(chunk) >= self._limit:
            self._data[:] = chunk[-self._limit :]
            return
        overflow = len(self._data) + len(chunk) - self._limit
        if overflow > 0:
            del self._data[:overflow]
        self._data.extend(chunk)

    @property
    def retained_bytes(self) -> int:
        return len(self._data)

    @property
    def truncated(self) -> bool:
        return self._total > len(self._data)


class AgyProcess:
    def __init__(
        self,
        config: AgyProcessConfig,
        process: asyncio.subprocess.Process,
    ) -> None:
        self._config = config
        self._process = process
        self._events: asyncio.Queue[AgyEvent | BackendProcessError | _ProcessEnd] = asyncio.Queue(
            maxsize=config.max_pending_events
        )
        self._stderr = _ByteRing(config.max_stderr_bytes)
        self._write_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()
        self._closed = False
        self._shutting_down = False
        self._discard_events = False
        self._root_exited = asyncio.Event()
        self._init_event: AgyInitEvent | None = None
        self._fault_cleanup: asyncio.Task[None] | None = None
        self._fault_cleanup_failed = False
        self._stdout_task = asyncio.create_task(
            self._read_stdout(),
            name="agy-acp.stdout",
        )
        self._stderr_task = asyncio.create_task(
            self._read_stderr(),
            name="agy-acp.stderr",
        )
        self._exit_task = asyncio.create_task(
            self._watch_exit(),
            name="agy-acp.exit",
        )

    @classmethod
    async def launch(
        cls,
        config: AgyProcessConfig,
        *,
        on_started: Callable[[AgyProcess], None] | None = None,
    ) -> AgyProcess:
        argv = build_agy_argv(
            config.command,
            conversation_id=config.conversation_id,
            additional_directories=config.additional_directories,
        )
        try:
            process = await cls._spawn(config, argv)
        except (OSError, RuntimeError, ValueError):
            raise BackendStartError from None
        instance = cls(config, process)
        try:
            if on_started is not None:
                on_started(instance)
            event = await instance._receive(
                timeout=config.init_timeout,
                before_initialization=True,
            )
            if not isinstance(event, AgyInitEvent):
                raise BackendProtocolError
            instance._init_event = event
            await asyncio.sleep(0)
            return instance
        except BaseException:
            await instance.cancel()
            raise

    @staticmethod
    async def _spawn(
        config: AgyProcessConfig,
        argv: tuple[str, ...],
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(config.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=config.max_line_bytes + 1,
            start_new_session=True,
        )

    @property
    def init_event(self) -> AgyInitEvent:
        if self._init_event is None:
            raise RuntimeError("process is not initialized")
        return self._init_event

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def closed(self) -> bool:
        tasks = [self._stdout_task, self._stderr_task, self._exit_task]
        if self._fault_cleanup is not None:
            tasks.append(self._fault_cleanup)
        return self._closed and all(task.done() for task in tasks)

    @property
    def pending_events(self) -> int:
        return self._events.qsize()

    @property
    def fault_cleanup_failed(self) -> bool:
        return self._fault_cleanup_failed

    @property
    def stderr_retained_bytes(self) -> int:
        return self._stderr.retained_bytes

    @property
    def stderr_truncated(self) -> bool:
        return self._stderr.truncated

    async def send(self, message: object) -> None:
        if self._closed or self._process.returncode is not None:
            raise BackendExitedError
        encoded = encode_json_line(message, max_line_bytes=self._config.max_line_bytes)
        writer = self._process.stdin
        if writer is None:
            await self.cancel()
            raise BackendWriteError
        try:
            async with self._write_lock:
                if self._closed or self._process.returncode is not None:
                    raise BackendExitedError
                writer.write(encoded)
                async with asyncio.timeout(self._config.write_timeout):
                    await writer.drain()
        except asyncio.CancelledError:
            await self.cancel()
            raise
        except BackendExitedError:
            await self.cancel()
            raise
        except Exception:
            await self.cancel()
            raise BackendWriteError from None

    async def receive(self, *, timeout: float) -> AgyEvent:
        if self._init_event is None:
            raise RuntimeError("process is not initialized")
        if self._closed and self._events.empty():
            raise BackendExitedError
        return await self._receive(timeout=timeout, before_initialization=False)

    async def _receive(
        self,
        *,
        timeout: float,
        before_initialization: bool,
    ) -> AgyEvent:
        if isinstance(timeout, bool) or not isinstance(timeout, int | float):
            raise ValueError("timeout must be positive and finite")
        try:
            timeout_seconds = float(timeout)
        except OverflowError:
            raise ValueError("timeout must be positive and finite") from None
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout must be positive and finite")
        try:
            async with asyncio.timeout(timeout_seconds):
                item = await self._events.get()
        except TimeoutError:
            await self.cancel()
            raise BackendTimeoutError from None
        if isinstance(item, BackendProcessError):
            await self.cancel()
            raise item
        if isinstance(item, _ProcessEnd):
            await self._run_shutdown(
                close_stdin=False,
                signal_process=False,
                discard_events=False,
            )
            raise BackendExitedError(before_initialization=before_initialization)
        return item

    async def cancel(self) -> None:
        await self._run_shutdown(
            close_stdin=False,
            signal_process=True,
            discard_events=True,
        )
        self._clear_events()

    async def close(self) -> None:
        await self._run_shutdown(
            close_stdin=True,
            signal_process=False,
            discard_events=True,
        )
        self._clear_events()

    async def _run_shutdown(
        self,
        *,
        close_stdin: bool,
        signal_process: bool,
        discard_events: bool,
    ) -> None:
        cleanup = asyncio.create_task(
            self._shutdown(
                close_stdin=close_stdin,
                signal_process=signal_process,
                discard_events=discard_events,
            ),
            name="agy-acp.shutdown",
        )
        cancelled = False
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                cancelled = True
        fault_cleanup = self._fault_cleanup
        current = asyncio.current_task()
        if fault_cleanup is not None and fault_cleanup is not current:
            while True:
                try:
                    await asyncio.shield(fault_cleanup)
                    break
                except asyncio.CancelledError:
                    cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _shutdown(
        self,
        *,
        close_stdin: bool,
        signal_process: bool,
        discard_events: bool,
    ) -> None:
        async with self._shutdown_lock:
            if self._closed:
                if discard_events:
                    self._discard_events = True
                    self._clear_events()
                return
            self._shutting_down = True
            if discard_events:
                self._discard_events = True
                self._clear_events()
            writer = self._process.stdin
            if close_stdin and writer is not None:
                writer.close()
            if signal_process and self._process.returncode is None:
                self._signal_gracefully()
            exited = await self._wait_for_exit(self._config.cancel_grace)
            if not exited:
                await self._kill_tree()
                exited = await self._wait_for_exit(self._config.kill_grace)
            if not exited:
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
                exited = await self._wait_for_exit(self._config.kill_grace)
            if not exited:
                raise BackendShutdownError
            await self._kill_tree()
            await self._join_io_tasks()
            if discard_events:
                self._clear_events()
            self._closed = True

    def _signal_gracefully(self) -> None:
        try:
            _kill_process_group(self._process.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError, ValueError):
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()

    async def _kill_tree(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            _kill_process_group(self._process.pid, _signal_by_name("SIGKILL"))

    async def _wait_for_exit(self, timeout: float) -> bool:
        if self._process.returncode is not None:
            return True
        try:
            async with asyncio.timeout(timeout):
                await asyncio.shield(self._process.wait())
            return True
        except TimeoutError:
            return False

    def _clear_events(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _join_tasks(self, tasks: list[asyncio.Task[None]]) -> None:
        try:
            async with asyncio.timeout(self._config.kill_grace):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _join_io_tasks(self) -> None:
        await self._join_tasks([self._stdout_task, self._stderr_task, self._exit_task])

    async def _read_stdout(self) -> None:
        reader = self._process.stdout
        if reader is None:
            self._publish_fault(BackendProtocolError())
            return
        parser = NdjsonParser(max_line_bytes=self._config.max_line_bytes)
        try:
            while chunk := await reader.read(min(64 * 1024, self._config.max_line_bytes + 1)):
                for payload in parser.feed(chunk):
                    event = parse_agy_event(payload)
                    if not isinstance(event, AgyUnknownEvent) and not self._discard_events:
                        await self._events.put(event)
            parser.finish()
            if not self._shutting_down:
                try:
                    async with asyncio.timeout(0.05):
                        await self._root_exited.wait()
                except TimeoutError:
                    self._publish_fault(BackendProtocolError())
        except Exception:
            self._publish_fault(BackendProtocolError())

    async def _watch_exit(self) -> None:
        while self._process.returncode is None:
            await asyncio.sleep(0.01)
        returncode = self._process.returncode
        self._root_exited.set()
        await self._kill_tree()
        await self._join_tasks([self._stdout_task, self._stderr_task])
        if not self._discard_events and not self._events.full():
            self._events.put_nowait(_ProcessEnd(returncode))
        self._closed = True

    async def _read_stderr(self) -> None:
        reader = self._process.stderr
        if reader is None:
            return
        while chunk := await reader.read(8192):
            self._stderr.append(chunk)

    def _publish_fault(self, error: BackendProcessError) -> None:
        if self._discard_events:
            return
        self._clear_events()
        self._events.put_nowait(error)
        self._schedule_fault_cleanup()

    def _schedule_fault_cleanup(self) -> None:
        if self._fault_cleanup is None:
            self._fault_cleanup = asyncio.create_task(
                self._run_shutdown(
                    close_stdin=False,
                    signal_process=True,
                    discard_events=False,
                ),
                name="agy-acp.fault-cleanup",
            )
            self._fault_cleanup.add_done_callback(self._observe_fault_cleanup)

    def _observe_fault_cleanup(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            self._fault_cleanup_failed = True


def _signal_by_name(name: str) -> int:
    value = signal.__dict__.get(name)
    if not isinstance(value, int):
        raise OSError("requested signal is unavailable")
    return value


def _kill_process_group(pid: int, requested_signal: int) -> None:
    kill_group: Any = os.__dict__.get("killpg")
    if kill_group is None:
        raise OSError("process-group signaling is unavailable")
    kill_group(pid, requested_signal)
