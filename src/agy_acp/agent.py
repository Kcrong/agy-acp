from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from acp import (
    PROTOCOL_VERSION,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    text_block,
)
from acp.helpers import update_agent_message
from acp.schema import (
    AgentCapabilities,
    Implementation,
    PromptCapabilities,
    ResourceContentBlock,
    TextContentBlock,
)
from pydantic import ValidationError

from agy_acp import __version__
from agy_acp.config import AgyProcessConfig
from agy_acp.errors import (
    AcpRequestError,
    BackendProcessError,
    BackendShutdownError,
    BackendTimeoutError,
    ProtocolEncodingError,
)
from agy_acp.events import AgyEvent, AgyResultEvent, AgyResultStatus, AgyStepUpdateEvent
from agy_acp.executable import AgyCommand
from agy_acp.process import AgyProcess

UpdateSender = Callable[[str, dict[str, object]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AgentConfig:
    command: AgyCommand
    max_line_bytes: int = 4 * 1024 * 1024
    max_stderr_bytes: int = 64 * 1024
    max_pending_events: int = 256
    max_sessions: int = 16
    init_timeout: float = 15.0
    write_timeout: float = 15.0
    prompt_timeout: float = 30 * 60.0
    response_barrier: float = 0.01
    cancel_grace: float = 5.0
    kill_grace: float = 2.0

    def __post_init__(self) -> None:
        if type(self.max_sessions) is not int or self.max_sessions <= 0:
            raise ValueError("max_sessions must be a positive integer")
        if isinstance(self.prompt_timeout, bool) or not isinstance(
            self.prompt_timeout, int | float
        ):
            raise ValueError("prompt_timeout must be positive and finite")
        try:
            timeout = float(self.prompt_timeout)
        except OverflowError:
            raise ValueError("prompt_timeout must be positive and finite") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("prompt_timeout must be positive and finite")
        if isinstance(self.response_barrier, bool) or not isinstance(
            self.response_barrier, int | float
        ):
            raise ValueError("response_barrier must be positive and finite")
        try:
            barrier = float(self.response_barrier)
        except OverflowError:
            raise ValueError("response_barrier must be positive and finite") from None
        if not math.isfinite(barrier) or barrier <= 0:
            raise ValueError("response_barrier must be positive and finite")


@dataclass(slots=True)
class _Session:
    process: AgyProcess
    active_prompt: asyncio.Task[object] | None = None
    cancel_requested: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_cleanup: asyncio.Task[None] | None = None


def _invalid_params() -> AcpRequestError:
    return AcpRequestError(-32602, "Invalid params")


def _backend_unavailable() -> AcpRequestError:
    return AcpRequestError(-32010, "Backend unavailable")


def _validate_field_meta(values: Mapping[str, object]) -> None:
    field_meta = values.get("_meta")
    if field_meta is not None and not isinstance(field_meta, Mapping):
        raise _invalid_params()


def _validate_annotations(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping) or set(value) - {
        "audience",
        "lastModified",
        "priority",
        "_meta",
    }:
        raise _invalid_params()
    _validate_field_meta(value)
    audience = value.get("audience")
    if audience is not None and (
        not isinstance(audience, list) or any(not isinstance(item, str) for item in audience)
    ):
        raise _invalid_params()
    last_modified = value.get("lastModified")
    if last_modified is not None and not isinstance(last_modified, str):
        raise _invalid_params()
    priority = value.get("priority")
    if priority is not None:
        if isinstance(priority, bool) or not isinstance(priority, int | float):
            raise _invalid_params()
        try:
            finite_priority = math.isfinite(float(priority))
        except OverflowError:
            finite_priority = False
        if not finite_priority:
            raise _invalid_params()


def _validate_optional_string(values: Mapping[str, object], key: str) -> None:
    value = values.get(key)
    if value is not None and not isinstance(value, str):
        raise _invalid_params()


def serialize_prompt(blocks: Sequence[Mapping[str, object]]) -> str:
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        try:
            if block_type == "text":
                if set(block) - {"type", "text", "annotations", "_meta"}:
                    raise _invalid_params()
                _validate_field_meta(block)
                _validate_annotations(block.get("annotations"))
                text = TextContentBlock.model_validate(block).text
                text.encode("utf-8", errors="strict")
                if text:
                    parts.append(text)
                continue
            if block_type == "resource_link":
                if set(block) - {
                    "type",
                    "annotations",
                    "description",
                    "mimeType",
                    "name",
                    "size",
                    "title",
                    "uri",
                    "_meta",
                }:
                    raise _invalid_params()
                _validate_field_meta(block)
                _validate_annotations(block.get("annotations"))
                for key in ("description", "mimeType", "title"):
                    _validate_optional_string(block, key)
                size = block.get("size")
                if size is not None and (
                    isinstance(size, bool) or not isinstance(size, int) or size < 0
                ):
                    raise _invalid_params()
                resource = ResourceContentBlock.model_validate(block)
                resource.name.encode("utf-8", errors="strict")
                resource.uri.encode("utf-8", errors="strict")
                if not resource.name or not resource.uri:
                    raise _invalid_params()
                parts.append(f"Resource: {resource.name} ({resource.uri})")
                continue
        except (UnicodeError, ValidationError):
            raise _invalid_params() from None
        raise _invalid_params()
    if not parts:
        raise _invalid_params()
    return "\n\n".join(parts)


class AgyAgent:
    def __init__(self, config: AgentConfig, send_update: UpdateSender) -> None:
        self._config = config
        self._send_update = send_update
        self._sessions: dict[str, _Session] = {}
        self._admission_lock = asyncio.Lock()
        self._starting_sessions = 0
        self._closing = False

    async def initialize(self, *, protocol_version: int) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                prompt_capabilities=PromptCapabilities(),
            ),
            agent_info=Implementation(name="agy-acp", version=__version__),
        )

    async def new_session(
        self,
        *,
        cwd: str,
        mcp_servers: list[object],
        additional_directories: list[str] | None = None,
    ) -> NewSessionResponse:
        if mcp_servers or additional_directories:
            raise _invalid_params()
        workspace = Path(cwd)
        if not workspace.is_absolute() or not workspace.is_dir():
            raise _invalid_params()
        process_config = AgyProcessConfig(
            command=self._config.command,
            cwd=workspace,
            max_line_bytes=self._config.max_line_bytes,
            max_stderr_bytes=self._config.max_stderr_bytes,
            max_pending_events=self._config.max_pending_events,
            init_timeout=self._config.init_timeout,
            write_timeout=self._config.write_timeout,
            cancel_grace=self._config.cancel_grace,
            kill_grace=self._config.kill_grace,
        )
        await self._reserve_session()
        reserved = True
        try:
            try:
                process = await AgyProcess.launch(process_config)
            except BackendTimeoutError:
                raise AcpRequestError(-32011, "Initialization timed out") from None
            except BackendProcessError:
                raise _backend_unavailable() from None
            try:
                if Path(process.init_event.cwd).resolve() != workspace.resolve():
                    raise _backend_unavailable()
                session_id = process.init_event.conversation_id
                async with self._admission_lock:
                    if self._closing or session_id in self._sessions:
                        raise _backend_unavailable()
                    self._sessions[session_id] = _Session(process)
                    self._starting_sessions -= 1
                    reserved = False
                return NewSessionResponse(session_id=session_id)
            except BaseException:
                await process.close()
                raise
        finally:
            if reserved:
                async with self._admission_lock:
                    self._starting_sessions -= 1

    async def _reserve_session(self) -> None:
        async with self._admission_lock:
            if self._closing:
                raise _backend_unavailable()
            if len(self._sessions) + self._starting_sessions >= self._config.max_sessions:
                raise AcpRequestError(-32014, "Session capacity exceeded")
            self._starting_sessions += 1

    def _remaining_prompt_time(self, deadline: float) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise BackendTimeoutError
        return remaining

    async def _send_prompt_before_deadline(
        self,
        session: _Session,
        message: object,
        deadline: float,
    ) -> None:
        try:
            async with asyncio.timeout(self._remaining_prompt_time(deadline)):
                await session.process.send(message)
        except TimeoutError:
            raise BackendTimeoutError from None

    async def _emit_delta_before_deadline(
        self,
        session_id: str,
        message_id: str,
        delta: str,
        deadline: float,
    ) -> None:
        try:
            async with asyncio.timeout(self._remaining_prompt_time(deadline)):
                await self._emit_delta(session_id, message_id, delta)
        except TimeoutError:
            raise BackendTimeoutError from None

    async def _verify_response_barrier(
        self,
        session: _Session,
        deadline: float,
    ) -> None:
        try:
            async with asyncio.timeout(self._remaining_prompt_time(deadline)):
                await asyncio.sleep(self._config.response_barrier)
        except TimeoutError:
            raise BackendTimeoutError from None
        if session.process.pending_events:
            raise _backend_unavailable()

    async def _terminate_session(self, session_id: str, session: _Session) -> None:
        await session.process.cancel()
        await self._retire_session(session_id, session)

    async def prompt(
        self,
        *,
        session_id: str,
        prompt: list[Mapping[str, object]],
    ) -> PromptResponse:
        session = self._sessions.get(session_id)
        if session is None:
            raise AcpRequestError(-32015, "Session not found")
        if session.active_prompt is not None:
            raise AcpRequestError(-32013, "Session busy")
        message = serialize_prompt(prompt)
        current = asyncio.current_task()
        if current is None:
            raise AcpRequestError(-32603, "Internal error")
        session.active_prompt = current
        session.cancel_requested = False
        session.cancel_event.clear()
        message_id = uuid4().hex
        chunks: list[str] = []
        streamed_bytes = 0
        deadline = asyncio.get_running_loop().time() + self._config.prompt_timeout
        try:
            await asyncio.sleep(0)
            if session.process.pending_events:
                raise _backend_unavailable()
            try:
                await self._send_prompt_before_deadline(
                    session,
                    {
                        "event": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": message}],
                        },
                    },
                    deadline,
                )
            except ProtocolEncodingError:
                raise _invalid_params() from None
            while True:
                remaining = self._remaining_prompt_time(deadline)
                event = await self._receive_or_cancel(session, remaining)
                if event is None:
                    return await self._finish_cancellation(session_id, session)
                if not isinstance(event, AgyStepUpdateEvent | AgyResultEvent):
                    raise _backend_unavailable()
                if event.conversation_id != session_id:
                    raise _backend_unavailable()
                if isinstance(event, AgyStepUpdateEvent):
                    if event.text_delta:
                        streamed_bytes += len(event.text_delta.encode("utf-8"))
                        if streamed_bytes > self._config.max_line_bytes:
                            raise _backend_unavailable()
                        chunks.append(event.text_delta)
                        await self._emit_delta_before_deadline(
                            session_id,
                            message_id,
                            event.text_delta,
                            deadline,
                        )
                    continue
                await self._verify_response_barrier(session, deadline)
                return await self._complete_result(
                    session,
                    session_id,
                    message_id,
                    chunks,
                    event,
                    deadline,
                )
        except asyncio.CancelledError:
            with contextlib.suppress(BackendProcessError):
                await self._terminate_session(session_id, session)
            raise
        except BackendTimeoutError:
            if self._is_cancelling(session):
                return await self._finish_cancellation(session_id, session)
            try:
                await self._terminate_session(session_id, session)
            except BackendProcessError:
                raise _backend_unavailable() from None
            raise AcpRequestError(-32012, "Prompt timed out") from None
        except BackendProcessError:
            if self._is_cancelling(session):
                return await self._finish_cancellation(session_id, session)
            await self._retire_session(session_id, session)
            raise _backend_unavailable() from None
        except AcpRequestError as error:
            if self._is_cancelling(session):
                return await self._finish_cancellation(session_id, session)
            if error.code != -32602:
                try:
                    await self._terminate_session(session_id, session)
                except BackendProcessError:
                    raise _backend_unavailable() from None
            raise
        except Exception:
            if self._is_cancelling(session):
                return await self._finish_cancellation(session_id, session)
            try:
                await self._terminate_session(session_id, session)
            except BackendProcessError:
                raise _backend_unavailable() from None
            raise AcpRequestError(-32603, "Internal error") from None
        finally:
            session.active_prompt = None

    async def _receive_or_cancel(
        self,
        session: _Session,
        timeout: float,
    ) -> AgyEvent | None:
        receive_task = asyncio.create_task(session.process.receive(timeout=timeout))
        cancel_task = asyncio.create_task(session.cancel_event.wait())
        tasks = (receive_task, cancel_task)
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                return receive_task.result()
            return None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start_cancel_cleanup(self, session: _Session) -> asyncio.Task[None]:
        cleanup = session.cancel_cleanup
        if cleanup is None:
            cleanup = asyncio.create_task(
                session.process.cancel(),
                name="agy-acp.session-cancel",
            )
            session.cancel_cleanup = cleanup
        return cleanup

    async def _await_cancel_cleanup(self, session: _Session) -> None:
        cleanup = session.cancel_cleanup
        if cleanup is None:
            raise BackendShutdownError
        await asyncio.shield(cleanup)

    async def _retire_session(self, session_id: str, session: _Session) -> None:
        async with self._admission_lock:
            if self._sessions.get(session_id) is session:
                self._sessions.pop(session_id)

    async def _finish_cancellation(
        self,
        session_id: str,
        session: _Session,
    ) -> PromptResponse:
        try:
            await self._await_cancel_cleanup(session)
        except BackendProcessError:
            await self._retire_session(session_id, session)
            raise _backend_unavailable() from None
        await self._retire_session(session_id, session)
        return PromptResponse(stop_reason="cancelled")

    @staticmethod
    def _is_cancelling(session: _Session) -> bool:
        return session.cancel_requested

    async def _complete_result(
        self,
        session: _Session,
        session_id: str,
        message_id: str,
        chunks: list[str],
        event: AgyResultEvent,
        deadline: float,
    ) -> PromptResponse:
        if self._is_cancelling(session):
            return await self._finish_cancellation(session_id, session)
        if event.status is AgyResultStatus.CANCELED:
            return PromptResponse(stop_reason="cancelled")
        if event.status is not AgyResultStatus.SUCCESS:
            raise _backend_unavailable()
        streamed = "".join(chunks)
        if not streamed:
            suffix = event.response
        elif event.response.startswith(streamed):
            suffix = event.response[len(streamed) :]
        else:
            raise _backend_unavailable()
        if suffix:
            await self._emit_delta_before_deadline(
                session_id,
                message_id,
                suffix,
                deadline,
            )
        if session.process.pending_events:
            raise _backend_unavailable()
        if self._is_cancelling(session):
            return await self._finish_cancellation(session_id, session)
        return PromptResponse(stop_reason="end_turn")

    async def _emit_delta(self, session_id: str, message_id: str, delta: str) -> None:
        update = update_agent_message(text_block(delta))
        update.message_id = message_id
        payload = update.model_dump(mode="json", by_alias=True, exclude_none=True)
        await self._send_update(session_id, payload)

    async def cancel(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.active_prompt is None:
            return
        session.cancel_requested = True
        cleanup = self._start_cancel_cleanup(session)
        session.cancel_event.set()
        await asyncio.shield(cleanup)
        await self._retire_session(session_id, session)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        sessions = list(self._sessions.values())
        self._sessions.clear()
        cleanups: list[asyncio.Task[None]] = []
        for session in sessions:
            session.cancel_requested = True
            cleanups.append(self._start_cancel_cleanup(session))
            session.cancel_event.set()
        results = await asyncio.gather(*cleanups, return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise BackendShutdownError
