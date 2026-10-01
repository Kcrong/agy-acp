from __future__ import annotations

import json
import tracemalloc

import pytest

from agy_acp.errors import (
    DuplicateKeyError,
    EmptyLineError,
    IncompleteLineError,
    InvalidUtf8Error,
    LineTooLongError,
    MalformedJsonError,
    NonObjectError,
    ParserStateError,
)
from agy_acp.ndjson import NdjsonParser


def encoded(value: object, *, newline: bytes = b"\n") -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + newline


def test_feed_decodes_multiple_records() -> None:
    parser = NdjsonParser(max_line_bytes=128)

    records = parser.feed(encoded({"value": 1}) + encoded({"value": 2}))

    assert records == [{"value": 1}, {"value": 2}]
    parser.finish()


def test_every_byte_split_preserves_utf8_record() -> None:
    line = encoded({"text": "한글🙂", "nested": {"value": 3}})

    for split in range(len(line) + 1):
        parser = NdjsonParser(max_line_bytes=len(line))
        records = parser.feed(line[:split]) + parser.feed(line[split:])
        parser.finish()
        assert records == [{"text": "한글🙂", "nested": {"value": 3}}]


def test_crlf_delimiter_is_excluded_from_exact_payload_limit() -> None:
    payload = b'{"value":"ok"}'
    line = payload + b"\r\n"

    for split in range(len(line) + 1):
        parser = NdjsonParser(max_line_bytes=len(payload))
        records = parser.feed(line[:split]) + parser.feed(line[split:])
        parser.finish()
        assert records == [{"value": "ok"}]


def test_empty_chunk_is_a_noop() -> None:
    parser = NdjsonParser(max_line_bytes=16)
    assert parser.feed(b"") == []
    parser.finish()


def test_empty_line_fails_closed() -> None:
    parser = NdjsonParser(max_line_bytes=16)

    with pytest.raises(EmptyLineError, match="Empty NDJSON record"):
        parser.feed(b"\n")
    with pytest.raises(ParserStateError, match="failed"):
        parser.feed(encoded({"later": True}))


def test_malformed_json_does_not_echo_input() -> None:
    parser = NdjsonParser(max_line_bytes=128)
    secret = "credential-sentinel"

    with pytest.raises(MalformedJsonError) as raised:
        parser.feed(f'{{"secret":"{secret}"\n'.encode())

    assert secret not in str(raised.value)


def test_invalid_utf8_is_distinct() -> None:
    parser = NdjsonParser(max_line_bytes=128)
    with pytest.raises(InvalidUtf8Error, match="UTF-8"):
        parser.feed(b'{"value":"\xff"}\n')


def test_json_scalar_or_array_is_rejected() -> None:
    parser = NdjsonParser(max_line_bytes=128)
    with pytest.raises(NonObjectError, match="object"):
        parser.feed(b"[]\n")


def test_duplicate_key_at_any_depth_is_rejected() -> None:
    parser = NdjsonParser(max_line_bytes=128)
    with pytest.raises(DuplicateKeyError, match="Duplicate"):
        parser.feed(b'{"nested":{"value":1,"value":2}}\n')


def test_non_standard_json_constant_is_rejected() -> None:
    parser = NdjsonParser(max_line_bytes=128)
    with pytest.raises(MalformedJsonError):
        parser.feed(b'{"value":NaN}\n')


def test_line_limit_is_enforced_before_newline() -> None:
    parser = NdjsonParser(max_line_bytes=10)
    with pytest.raises(LineTooLongError, match="limit"):
        parser.feed(b"x" * 11)


def test_line_exactly_at_limit_is_accepted() -> None:
    payload = b'{"v":1}'
    parser = NdjsonParser(max_line_bytes=len(payload))
    assert parser.feed(payload + b"\n") == [{"v": 1}]
    parser.finish()


def test_unterminated_eof_is_distinct_and_fails_closed() -> None:
    parser = NdjsonParser(max_line_bytes=128)
    assert parser.feed(b'{"value":1}') == []

    with pytest.raises(IncompleteLineError, match="Unterminated"):
        parser.finish()
    with pytest.raises(ParserStateError, match="failed"):
        parser.finish()


def test_clean_finish_closes_parser() -> None:
    parser = NdjsonParser(max_line_bytes=16)
    parser.finish()
    with pytest.raises(ParserStateError, match="closed"):
        parser.feed(b"")


@pytest.mark.parametrize("limit", [0, True, 1.5, float("nan"), float("inf"), "64"])
def test_maximum_line_size_must_be_positive_integer(limit: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        NdjsonParser(max_line_bytes=limit)  # type: ignore[arg-type]


def test_one_byte_feeds_preserve_multiple_records_and_crlf() -> None:
    stream = encoded({"first": "한글"}, newline=b"\r\n") + encoded({"second": 2})
    parser = NdjsonParser(max_line_bytes=128)
    records: list[dict[str, object]] = []

    for byte in stream:
        records.extend(parser.feed(bytes([byte])))

    parser.finish()
    assert records == [{"first": "한글"}, {"second": 2}]


def test_lone_unicode_surrogate_is_rejected() -> None:
    parser = NdjsonParser(max_line_bytes=128)
    with pytest.raises(MalformedJsonError):
        parser.feed(b'{"value":"\\ud800"}\n')


def test_overflowing_json_number_is_rejected() -> None:
    parser = NdjsonParser(max_line_bytes=128)
    with pytest.raises(MalformedJsonError):
        parser.feed(b'{"value":1e999}\n')


def test_excessive_json_nesting_is_a_fixed_parse_error() -> None:
    record = (b'{"nested":' * 1500) + b"null" + (b"}" * 1500) + b"\n"
    parser = NdjsonParser(max_line_bytes=len(record))

    with pytest.raises(MalformedJsonError, match="Malformed JSON record"):
        parser.feed(record)


def test_oversized_terminated_record_is_rejected_before_copying() -> None:
    parser = NdjsonParser(max_line_bytes=64)
    record = (b"x" * (1024 * 1024)) + b"\n"

    tracemalloc.start()
    try:
        with pytest.raises(LineTooLongError):
            parser.feed(record)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 256 * 1024


@pytest.mark.parametrize("data", [bytearray(b"{}\n"), memoryview(b"{}\n"), "{}\n", None])
def test_feed_requires_bytes_without_poisoning_parser(data: object) -> None:
    parser = NdjsonParser(max_line_bytes=16)

    with pytest.raises(TypeError, match="data must be bytes"):
        parser.feed(data)  # type: ignore[arg-type]

    assert parser.feed(b'{"ok":true}\n') == [{"ok": True}]
    parser.finish()


def _nested_objects(count: int, leaf: object = None) -> bytes:
    value = leaf
    for _ in range(count):
        value = {"nested": value}
    return encoded(value)


def _nested_arrays_inside_object(count: int) -> bytes:
    value: object = None
    for _ in range(count):
        value = [value]
    return encoded({"nested": value})


def test_sixty_four_containers_are_allowed() -> None:
    for record in (_nested_objects(64), _nested_arrays_inside_object(63)):
        parser = NdjsonParser(max_line_bytes=len(record))
        assert len(parser.feed(record)) == 1
        parser.finish()


def test_sixty_five_containers_are_rejected_for_any_leaf() -> None:
    records = (
        _nested_objects(65),
        _nested_objects(64, {}),
        _nested_arrays_inside_object(64),
    )
    for record in records:
        parser = NdjsonParser(max_line_bytes=len(record))
        with pytest.raises(MalformedJsonError):
            parser.feed(record)
