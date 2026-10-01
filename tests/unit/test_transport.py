from __future__ import annotations

import io
import json

import pytest

from agy_acp.diagnostics import DiagnosticCode, DiagnosticSink
from agy_acp.errors import ProtocolEncodingError
from agy_acp.transport import encode_json_line


def test_protocol_line_is_one_utf8_json_object() -> None:
    encoded = encode_json_line(
        {"jsonrpc": "2.0", "id": 1, "result": {"text": "line one\n한글🙂"}},
        max_line_bytes=256,
    )

    assert encoded.endswith(b"\n")
    assert b"\n" not in encoded[:-1]
    assert json.loads(encoded) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"text": "line one\n한글🙂"},
    }


def test_protocol_line_limit_excludes_delimiter() -> None:
    message = {"value": 1}
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
    assert encode_json_line(message, max_line_bytes=len(payload)) == payload + b"\n"
    with pytest.raises(ProtocolEncodingError, match="line limit"):
        encode_json_line(message, max_line_bytes=len(payload) - 1)


@pytest.mark.parametrize(
    "message",
    [
        ["not", "an", "object"],
        {1: "non-string-key"},
        {"value": object()},
        {"value": float("nan")},
        {"value": "\ud800"},
    ],
)
def test_protocol_encoder_rejects_invalid_values_without_echo(
    message: object,
) -> None:
    with pytest.raises(ProtocolEncodingError, match="Protocol message") as raised:
        encode_json_line(message, max_line_bytes=256)

    assert "object at" not in str(raised.value)
    assert "nan" not in str(raised.value).lower()


@pytest.mark.parametrize("limit", [0, True, 1.5, float("nan"), float("inf"), "64"])
def test_protocol_line_size_must_be_positive_integer(limit: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        encode_json_line({}, max_line_bytes=limit)  # type: ignore[arg-type]


def test_diagnostics_emit_only_fixed_codes() -> None:
    stream = io.BytesIO()
    diagnostics = DiagnosticSink(stream, max_bytes=128)

    assert diagnostics.emit(DiagnosticCode.TRANSPORT_ERROR)
    assert diagnostics.emit(DiagnosticCode.BACKEND_ERROR)

    assert stream.getvalue() == (b"agy-acp: transport error\nagy-acp: backend error\n")
    assert not diagnostics.truncated


def test_diagnostics_reject_arbitrary_text() -> None:
    stream = io.BytesIO()
    diagnostics = DiagnosticSink(stream, max_bytes=128)
    secret = "credential-sentinel"

    with pytest.raises(TypeError, match="DiagnosticCode"):
        diagnostics.emit(secret)  # type: ignore[arg-type]

    assert secret.encode() not in stream.getvalue()


def test_diagnostics_are_bounded_and_mark_truncation_once() -> None:
    stream = io.BytesIO()
    diagnostics = DiagnosticSink(stream, max_bytes=48)

    for _ in range(10):
        diagnostics.emit(DiagnosticCode.BACKEND_ERROR)

    output = stream.getvalue()
    assert len(output) <= 48
    assert output.count(b"diagnostics truncated") <= 1
    assert diagnostics.truncated


def test_diagnostic_limit_must_fit_truncation_marker() -> None:
    with pytest.raises(ValueError, match="too small"):
        DiagnosticSink(io.BytesIO(), max_bytes=8)


def test_protocol_encoder_rejects_recursive_objects() -> None:
    message: dict[str, object] = {}
    message["self"] = message

    with pytest.raises(ProtocolEncodingError, match="Protocol message"):
        encode_json_line(message, max_line_bytes=256)


def test_diagnostic_truncation_marker_is_emitted_once() -> None:
    stream = io.BytesIO()
    diagnostics = DiagnosticSink(stream, max_bytes=64)

    for _ in range(10):
        diagnostics.emit(DiagnosticCode.BACKEND_ERROR)

    output = stream.getvalue()
    assert len(output) <= 64
    assert output.count(b"diagnostics truncated") == 1
    assert diagnostics.truncated


@pytest.mark.parametrize("limit", [True, 3.5, "64"])
def test_diagnostic_limit_must_be_an_integer(limit: object) -> None:
    with pytest.raises(ValueError, match="integer"):
        DiagnosticSink(io.BytesIO(), max_bytes=limit)  # type: ignore[arg-type]


class _ShortWriteStream:
    def __init__(self, chunk_size: int) -> None:
        self.chunk_size = chunk_size
        self.data = bytearray()

    def write(self, value: bytes) -> int:
        written = min(self.chunk_size, len(value))
        self.data.extend(value[:written])
        return written

    def flush(self) -> None:
        return None


class _ZeroWriteStream:
    def write(self, _value: bytes) -> int:
        return 0

    def flush(self) -> None:
        return None


def test_diagnostics_handle_legal_short_writes_within_limit() -> None:
    stream = _ShortWriteStream(chunk_size=3)
    diagnostics = DiagnosticSink(stream, max_bytes=64)  # type: ignore[arg-type]

    for _ in range(10):
        diagnostics.emit(DiagnosticCode.BACKEND_ERROR)

    output = bytes(stream.data)
    assert len(output) <= 64
    assert output.count(b"diagnostics truncated") == 1
    assert diagnostics.truncated


def test_diagnostics_fail_closed_after_zero_write() -> None:
    stream = _ZeroWriteStream()
    diagnostics = DiagnosticSink(stream, max_bytes=64)  # type: ignore[arg-type]

    with pytest.raises(OSError, match="diagnostic stream write failed"):
        diagnostics.emit(DiagnosticCode.BACKEND_ERROR)

    assert not diagnostics.emit(DiagnosticCode.TRANSPORT_ERROR)
