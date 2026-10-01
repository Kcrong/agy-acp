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


class ExecutableResolutionError(AgyAcpError):
    """Raised when the configured backend executable is unsafe or unavailable."""


class BackendProcessError(AgyAcpError):
    """Base exception for backend process lifecycle failures."""


class BackendStartError(BackendProcessError):
    def __init__(self) -> None:
        super().__init__("Backend could not start")


class BackendProtocolError(BackendProcessError):
    def __init__(self) -> None:
        super().__init__("Backend produced invalid output")


class BackendExitedError(BackendProcessError):
    def __init__(self, *, before_initialization: bool = False) -> None:
        message = (
            "Backend exited before initialization"
            if before_initialization
            else "Backend exited unexpectedly"
        )
        super().__init__(message)


class BackendTimeoutError(BackendProcessError):
    def __init__(self) -> None:
        super().__init__("Backend operation timed out")


class BackendWriteError(BackendProcessError):
    def __init__(self) -> None:
        super().__init__("Backend input failed")


class BackendShutdownError(BackendProcessError):
    def __init__(self) -> None:
        super().__init__("Backend shutdown failed")


class AcpRequestError(AgyAcpError):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class ProtocolWriteError(AgyAcpError):
    """Raised after protocol output becomes indeterminate or unavailable."""


class InvalidMcpConfigError(AgyAcpError):
    def __init__(self) -> None:
        super().__init__("Invalid MCP server configuration")


class McpHandoffError(AgyAcpError):
    def __init__(self) -> None:
        super().__init__("MCP handoff failed")
