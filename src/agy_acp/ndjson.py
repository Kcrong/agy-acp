from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import NoReturn

from agy_acp.errors import (
    DuplicateKeyError,
    EmptyLineError,
    IncompleteLineError,
    InvalidUtf8Error,
    LineTooLongError,
    MalformedJsonError,
    NdjsonError,
    NonObjectError,
    ParserStateError,
)


class _DuplicateKey(ValueError):
    pass


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError


def _strict_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError
        return
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, list):
        container_depth = depth + 1
        if container_depth > 64:
            raise ValueError
        for nested in value:
            _validate_json_value(nested, depth=container_depth)
        return
    if isinstance(value, dict):
        container_depth = depth + 1
        if container_depth > 64:
            raise ValueError
        for key, nested in value.items():
            key.encode("utf-8", errors="strict")
            _validate_json_value(nested, depth=container_depth)
        return
    raise ValueError


def _decode_record(record: bytes) -> dict[str, object]:
    if not record:
        raise EmptyLineError
    try:
        text = record.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise InvalidUtf8Error from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        _validate_json_value(value)
    except _DuplicateKey:
        raise DuplicateKeyError from None
    except (json.JSONDecodeError, RecursionError, UnicodeError, ValueError):
        raise MalformedJsonError from None
    if not isinstance(value, dict):
        raise NonObjectError
    return value


class NdjsonParser:
    """Decode newline-delimited JSON objects while bounding retained bytes."""

    def __init__(self, *, max_line_bytes: int) -> None:
        if type(max_line_bytes) is not int or max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be a positive integer")
        self._max_line_bytes = max_line_bytes
        self._buffer = bytearray()
        self._state = "open"

    def feed(self, data: bytes) -> list[dict[str, object]]:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        self._require_open()
        records: list[dict[str, object]] = []
        offset = 0
        try:
            while offset < len(data):
                newline = data.find(b"\n", offset)
                complete = newline >= 0
                end = len(data) if not complete else newline
                segment_length = end - offset
                combined_length = len(self._buffer) + segment_length
                ends_with_cr = (
                    data[end - 1] == 13
                    if segment_length > 0
                    else bool(self._buffer and self._buffer[-1] == 13)
                )
                payload_length = combined_length - int(ends_with_cr)
                allowed_retained = self._max_line_bytes + int(ends_with_cr)
                if payload_length > self._max_line_bytes or combined_length > allowed_retained:
                    raise LineTooLongError
                if segment_length:
                    self._buffer.extend(data[offset:end])
                if not complete:
                    break
                record = bytes(self._buffer)
                self._buffer.clear()
                if ends_with_cr:
                    record = record[:-1]
                records.append(_decode_record(record))
                offset = newline + 1
        except NdjsonError:
            self._fail()
            raise
        return records

    def finish(self) -> None:
        self._require_open()
        if self._buffer:
            self._fail()
            raise IncompleteLineError
        self._state = "closed"

    def _require_open(self) -> None:
        if self._state != "open":
            raise ParserStateError(self._state)

    def _fail(self) -> None:
        self._buffer.clear()
        self._state = "failed"
