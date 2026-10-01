from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from agy_acp.executable import AgyCommand


def _positive_integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _positive_seconds(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be positive and finite")
    try:
        seconds = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be positive and finite") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{name} must be positive and finite")


@dataclass(frozen=True, slots=True)
class AgyProcessConfig:
    command: AgyCommand
    cwd: Path
    additional_directories: tuple[Path, ...] = ()
    conversation_id: str | None = None
    max_line_bytes: int = 4 * 1024 * 1024
    max_stderr_bytes: int = 64 * 1024
    max_pending_events: int = 256
    init_timeout: float = 15.0
    write_timeout: float = 15.0
    cancel_grace: float = 5.0
    kill_grace: float = 2.0

    def __post_init__(self) -> None:
        if not isinstance(self.command, AgyCommand):
            raise ValueError("command must be an AgyCommand")
        if not self.cwd.is_absolute() or not self.cwd.is_dir():
            raise ValueError("cwd must be an existing absolute directory")
        for directory in self.additional_directories:
            if not directory.is_absolute():
                raise ValueError("additional directories must be absolute")
        if self.conversation_id is not None and (
            not self.conversation_id or "\x00" in self.conversation_id
        ):
            raise ValueError("conversation_id must be nonempty and contain no NUL")
        _positive_integer("max_line_bytes", self.max_line_bytes)
        _positive_integer("max_stderr_bytes", self.max_stderr_bytes)
        _positive_integer("max_pending_events", self.max_pending_events)
        _positive_seconds("init_timeout", self.init_timeout)
        _positive_seconds("write_timeout", self.write_timeout)
        _positive_seconds("cancel_grace", self.cancel_grace)
        _positive_seconds("kill_grace", self.kill_grace)
