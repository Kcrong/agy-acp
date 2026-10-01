from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping

from acp.schema import InitializeRequest
from pydantic import BaseModel, ValidationError

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


def _same_json_shape(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_json_shape(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_json_shape(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


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
        max_in_flight: int = 256,
    ) -> None:
        if type(max_in_flight) is not int or max_in_flight <= 0:
            raise ValueError("max_in_flight must be a positive integer")
        self._agent = agent
        self._reader = reader
        self._writer = writer
        self._max_line_bytes = max_line_bytes
        self._write_timeout = write_timeout
        self._max_in_flight = max_in_flight
        self._write_lock = asyncio.Lock()
        self._requests: dict[RequestId, asyncio.Task[None]] = {}
        self._initialized = False
        self._closing = False

    async def serve(self) -> None:
        parser = NdjsonParser(max_line_bytes=self._max_line_bytes)
        discarding_oversized_record = False
        try:
            while chunk := await self._reader.read(min(64 * 1024, self._max_line_bytes + 1)):
                offset = 0
                while offset < len(chunk):
                    newline = chunk.find(b"\n", offset)
                    complete = newline >= 0
                    end = newline + 1 if complete else len(chunk)
                    segment = chunk[offset:end]
                    offset = end
                    if discarding_oversized_record:
                        if complete:
                            discarding_oversized_record = False
                            parser = NdjsonParser(max_line_bytes=self._max_line_bytes)
                        continue
                    try:
                        messages = parser.feed(segment)
                    except NdjsonError as error:
                        await self._send_parser_error(error)
                        parser = NdjsonParser(max_line_bytes=self._max_line_bytes)
                        discarding_oversized_record = not complete and isinstance(
                            error, LineTooLongError
                        )
                        continue
                    for message in messages:
                        await self._accept(message)
            if not discarding_oversized_record:
                try:
                    parser.finish()
                except NdjsonError as error:
                    await self._send_parser_error(error)
        finally:
            await self._close()

    async def _send_parser_error(self, error: NdjsonError) -> None:
        code = -32600 if isinstance(error, (LineTooLongError, NonObjectError)) else -32700
        await self._send_error(
            None,
            code,
            "Invalid request" if code == -32600 else "Parse error",
        )

    async def _accept(self, message: Mapping[str, object]) -> None:
        try:
            request_id, method = self._validate_envelope(message)
        except AcpRequestError as error:
            await self._send_error(None, error.code, error.message)
            return

        if request_id is None:
            await self._run_notification(method, message)
            return
        if request_id in self._requests or len(self._requests) >= self._max_in_flight:
            await self._send_error(request_id, -32600, "Invalid request")
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
            await self._send_result_uninterruptibly(request_id, result)
        except asyncio.CancelledError:
            if not self._closing:
                await self._send_error_uninterruptibly(
                    request_id,
                    -32800,
                    "Request cancelled",
                )
            return
        except AcpRequestError as error:
            await self._send_error_uninterruptibly(
                request_id,
                error.code,
                error.message,
            )
            return
        except Exception:
            await self._send_error_uninterruptibly(
                request_id,
                -32603,
                "Internal error",
            )
            return

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
            try:
                request = InitializeRequest.model_validate(params)
                normalized = request.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_unset=True,
                    warnings=False,
                )
            except ValidationError:
                raise _invalid_params() from None
            if not _same_json_shape(dict(params), normalized):
                raise _invalid_params()
            result = await self._agent.initialize(protocol_version=request.protocol_version)
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

    async def _send_error_uninterruptibly(
        self,
        request_id: RequestId,
        code: int,
        message: str,
    ) -> None:
        sending = asyncio.create_task(
            self._send_error(request_id, code, message),
            name="agy-acp.error-response",
        )
        while True:
            try:
                await asyncio.shield(sending)
                return
            except asyncio.CancelledError:
                if self._closing:
                    sending.cancel()
                    await asyncio.gather(sending, return_exceptions=True)
                    raise
                if sending.done():
                    return sending.result()

    async def _send_result_uninterruptibly(
        self,
        request_id: RequestId,
        result: BaseModel,
    ) -> None:
        sending = asyncio.create_task(
            self._send_result(request_id, result),
            name="agy-acp.response",
        )
        while True:
            try:
                await asyncio.shield(sending)
                return
            except asyncio.CancelledError:
                if self._closing:
                    sending.cancel()
                    await asyncio.gather(sending, return_exceptions=True)
                    raise
                if sending.done():
                    return sending.result()

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

    async def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        requests = list(self._requests.values())
        for task in requests:
            task.cancel()
        cleanup_error: BaseException | None = None
        try:
            await self._agent.close()
        except BaseException as error:
            cleanup_error = error
        await asyncio.gather(*requests, return_exceptions=True)
        self._requests.clear()
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()
        if cleanup_error is not None:
            raise cleanup_error
