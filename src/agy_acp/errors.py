from __future__ import annotations


class AgyAcpError(Exception):
    """Base exception for adapter-owned failures."""


class NdjsonError(AgyAcpError):
    """Base exception for strict NDJSON decoding failures."""


class EmptyLineError(NdjsonError):
    def __init__(self) -> None:
        super().__init__("Empty NDJSON record")


class InvalidUtf8Error(NdjsonError):
    def __init__(self) -> None:
        super().__init__("NDJSON record is not valid UTF-8")


class MalformedJsonError(NdjsonError):
    def __init__(self) -> None:
        super().__init__("Malformed JSON record")


class DuplicateKeyError(NdjsonError):
    def __init__(self) -> None:
        super().__init__("Duplicate JSON object key")


class NonObjectError(NdjsonError):
    def __init__(self) -> None:
        super().__init__("NDJSON record must be a JSON object")


class LineTooLongError(NdjsonError):
    def __init__(self) -> None:
        super().__init__("NDJSON record exceeds the line limit")


class IncompleteLineError(NdjsonError):
    def __init__(self) -> None:
        super().__init__("Unterminated NDJSON record at EOF")


class ParserStateError(NdjsonError):
    def __init__(self, state: str) -> None:
        super().__init__(f"NDJSON parser is {state}")


class InvalidEventError(AgyAcpError):
    def __init__(self) -> None:
        super().__init__("Invalid agy event")


class ProtocolEncodingError(AgyAcpError):
    """Raised when a protocol message cannot be encoded safely."""
