from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from agy_acp.errors import ProtocolEncodingError


def _validate_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ProtocolEncodingError("Protocol message contains a non-string key")
            _validate_keys(nested)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _validate_keys(nested)


def encode_json_line(message: object, *, max_line_bytes: int) -> bytes:
    """Encode one JSON object without permitting physical embedded newlines."""
    if type(max_line_bytes) is not int or max_line_bytes <= 0:
        raise ValueError("max_line_bytes must be a positive integer")
    if not isinstance(message, Mapping):
        raise ProtocolEncodingError("Protocol message must be a JSON object")
    try:
        _validate_keys(message)
        encoded = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except ProtocolEncodingError:
        raise
    except (RecursionError, TypeError, UnicodeError, ValueError):
        raise ProtocolEncodingError("Protocol message is not JSON serializable") from None
    if len(encoded) > max_line_bytes:
        raise ProtocolEncodingError("Protocol message exceeds the line limit")
    return encoded + b"\n"
