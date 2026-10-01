from __future__ import annotations

from enum import StrEnum
from typing import BinaryIO


class DiagnosticCode(StrEnum):
    TRANSPORT_ERROR = "transport error"
    BACKEND_ERROR = "backend error"


_PREFIX = b"agy-acp: "
_TRUNCATION_LINE = _PREFIX + b"diagnostics truncated\n"


class DiagnosticSink:
    """Write fixed diagnostics without accepting payload-derived text."""

    def __init__(self, stream: BinaryIO, *, max_bytes: int) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise ValueError("diagnostic byte limit must be an integer")
        if max_bytes < len(_TRUNCATION_LINE):
            raise ValueError("diagnostic byte limit is too small")
        self._stream = stream
        self._max_bytes = max_bytes
        self._written = 0
        self._truncated = False

    @property
    def truncated(self) -> bool:
        return self._truncated

    def emit(self, code: DiagnosticCode) -> bool:
        if not isinstance(code, DiagnosticCode):
            raise TypeError("code must be a DiagnosticCode")
        if self._truncated:
            return False
        line = _PREFIX + code.encode() + b"\n"
        if self._written + len(line) + len(_TRUNCATION_LINE) <= self._max_bytes:
            self._write(line)
            return True
        self._truncated = True
        if self._written + len(_TRUNCATION_LINE) <= self._max_bytes:
            self._write(_TRUNCATION_LINE)
        return False

    def _write(self, value: bytes) -> None:
        written = self._stream.write(value)
        if written != len(value):
            raise OSError("diagnostic stream performed a partial write")
        self._stream.flush()
        self._written += written
