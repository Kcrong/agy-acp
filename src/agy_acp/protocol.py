from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from agy_acp.agent import AgyAgent
from agy_acp.errors import (
    AcpRequestError,
    LineTooLongError,
    NdjsonError,
    NonObjectError,
)
from agy_acp.ndjson import NdjsonParser
from agy_acp.transport import encode_json_line

__all__ = ["AcpRequestError", "AcpStdioServer"]

RequestId = int | str


def _invalid_request() -> AcpRequestError:
    return AcpRequestError(-32600, "Invalid request")


def _invalid_params() -> AcpRequestError:
    return AcpRequestError(-32602, "Invalid params")


def _params(message: Mapping[str, object]) -> Mapping[str, object]:
    params = message.get("params")
    if not isinstance(params, Mapping):
        raise _invalid_params()
    return params


def _validate_params(
    values: Mapping[str, object],
    allowed: set[str],
) -> None:
    if set(values) - allowed:
        raise _invalid_params()
    field_meta = values.get("_meta")
    if field_meta is not None and not isinstance(field_meta, Mapping):
        raise _invalid_params()


def _required_string(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise _invalid_params()
    return value


def _strict_string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _invalid_params()
    return value


class AcpStdioServer:
    def __init__(
        self,
        agent: AgyAgent,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        max_line_bytes: int,
        write_timeout: float = 15.0,
    ) -> None:
        self._agent = agent
        self._reader = reader
        self._writer = writer
        self._max_line_bytes = max_line_bytes
        self._write_timeout = write_timeout
        self._write_lock = asyncio.Lock()
        self._requests: dict[RequestId, asyncio.Task[None]] = {}
        self._notifications: set[asyncio.Task[None]] = set()
        self._initialized = False
        self._closing = False

    async def serve(self) -> None:
        parser = NdjsonParser(max_line_bytes=self._max_line_bytes)
        try:
            while chunk := await self._reader.read(min(64 * 1024, self._max_line_bytes + 1)):
                for message in parser.feed(chunk):
                    self._accept(message)
            parser.finish()
        except NdjsonError as error:
            code = -32600 if isinstance(error, (LineTooLongError, NonObjectError)) else -32700
            await self._send_error(
                None, code, "Invalid request" if code == -32600 else "Parse error"
            )
        finally:
            await self._close()

    def _accept(self, message: Mapping[str, object]) -> None:
        try:
            request_id, method = self._validate_envelope(message)
        except AcpRequestError as error:
            self._track_notification(
                self._send_error(None, error.code, error.message),
                "agy-acp.invalid-request",
            )
            return

        if request_id is None:
            self._track_notification(
                self._run_notification(method, message),
                f"agy-acp.notification.{method}",
            )
            return
        if request_id in self._requests:
            self._track_notification(
                self._send_error(request_id, -32600, "Invalid request"),
                "agy-acp.duplicate-request",
            )
            return
        task = asyncio.create_task(
            self._run_request(request_id, method, message),
            name=f"agy-acp.request.{method}",
        )
        self._requests[request_id] = task

        def remove_request(completed: asyncio.Task[None]) -> None:
            if self._requests.get(request_id) is completed:
                self._requests.pop(request_id, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(remove_request)

    @staticmethod
    def _validate_envelope(message: Mapping[str, object]) -> tuple[RequestId | None, str]:
        if set(message) - {"jsonrpc", "id", "method", "params"}:
            raise _invalid_request()
        if message.get("jsonrpc") != "2.0":
            raise _invalid_request()
        method = message.get("method")
        if not isinstance(method, str) or not method:
            raise _invalid_request()
        if "id" not in message:
            return None, method
        request_id = message["id"]
        if isinstance(request_id, bool) or not isinstance(request_id, int | str):
            raise _invalid_request()
        return request_id, method

    async def _run_request(
        self,
        request_id: RequestId,
        method: str,
        message: Mapping[str, object],
    ) -> None:
        try:
            result = await self._dispatch_request(method, message)
        except asyncio.CancelledError:
            if not self._closing:
                await self._send_error(request_id, -32800, "Request cancelled")
            return
        except AcpRequestError as error:
            await self._send_error(request_id, error.code, error.message)
            return
        except Exception:
            await self._send_error(request_id, -32603, "Internal error")
            return
        await self._send_result(request_id, result)

    async def _dispatch_request(
        self,
        method: str,
        message: Mapping[str, object],
    ) -> BaseModel:
        if method not in {"initialize", "session/new", "session/prompt"}:
            raise AcpRequestError(-32601, "Method not found")
        params = _params(message)
        if method == "initialize":
            _validate_params(
                params,
                {"protocolVersion", "clientCapabilities", "clientInfo", "_meta"},
            )
            if self._initialized:
                raise _invalid_request()
            protocol_version = params.get("protocolVersion")
            if (
                isinstance(protocol_version, bool)
                or not isinstance(protocol_version, int)
                or not 0 <= protocol_version <= 65535
            ):
                raise _invalid_params()
            for key in ("clientCapabilities", "clientInfo"):
                value = params.get(key)
                if value is not None and not isinstance(value, Mapping):
                    raise _invalid_params()
            result = await self._agent.initialize(protocol_version=protocol_version)
            self._initialized = True
            return result
        if not self._initialized:
            raise _invalid_request()
        if method == "session/new":
            _validate_params(
                params,
                {"cwd", "mcpServers", "additionalDirectories", "_meta"},
            )
            cwd = _required_string(params, "cwd")
            mcp_servers = params.get("mcpServers")
            if not isinstance(mcp_servers, list):
                raise _invalid_params()
            additional = _strict_string_list(params.get("additionalDirectories"))
            return await self._agent.new_session(
                cwd=cwd,
                mcp_servers=list(mcp_servers),
                additional_directories=additional,
            )
        _validate_params(params, {"sessionId", "prompt", "_meta"})
        session_id = _required_string(params, "sessionId")
        prompt = params.get("prompt")
        if not isinstance(prompt, list) or any(not isinstance(item, Mapping) for item in prompt):
            raise _invalid_params()
        return await self._agent.prompt(
            session_id=session_id,
            prompt=list(prompt),
        )

    async def _run_notification(
        self,
        method: str,
        message: Mapping[str, object],
    ) -> None:
        try:
            if method not in {"session/cancel", "$/cancel_request"}:
                return
            params = _params(message)
            if method == "session/cancel":
                _validate_params(params, {"sessionId", "_meta"})
                await self._agent.cancel(_required_string(params, "sessionId"))
                return
            _validate_params(params, {"requestId"})
            request_id = params.get("requestId")
            if isinstance(request_id, bool) or not isinstance(request_id, int | str):
                raise _invalid_params()
            task = self._requests.get(request_id)
            if task is not None:
                task.cancel()
        except Exception:
            return

    async def send_session_update(
        self,
        session_id: str,
        update: dict[str, object],
    ) -> None:
        await self._send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": session_id, "update": update},
            }
        )

    async def _send_result(self, request_id: RequestId, result: BaseModel) -> None:
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                ),
            }
        )

    async def _send_error(
        self,
        request_id: RequestId | None,
        code: int,
        message: str,
    ) -> None:
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    async def _send(self, message: dict[str, object]) -> None:
        if self._closing:
            return
        encoded = encode_json_line(message, max_line_bytes=self._max_line_bytes)
        async with self._write_lock:
            self._writer.write(encoded)
            async with asyncio.timeout(self._write_timeout):
                await self._writer.drain()

    def _track_notification(self, awaitable: Any, name: str) -> None:
        task = asyncio.create_task(awaitable, name=name)
        self._notifications.add(task)

        def remove_notification(completed: asyncio.Task[None]) -> None:
            self._notifications.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(remove_notification)

    async def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        requests = list(self._requests.values())
        notifications = list(self._notifications)
        for task in requests:
            task.cancel()
        cleanup_error: BaseException | None = None
        try:
            await self._agent.close()
        except BaseException as error:
            cleanup_error = error
        await asyncio.gather(*requests, *notifications, return_exceptions=True)
        self._requests.clear()
        self._notifications.clear()
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()
        if cleanup_error is not None:
            raise cleanup_error
