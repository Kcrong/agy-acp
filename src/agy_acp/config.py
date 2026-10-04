from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agy_acp.executable import AgyCommand
from agy_acp.models import AGY_EFFORTS, AgyEffort

_MAX_PENDING_EVENT_BYTES = 16 * 1024 * 1024
_MAX_ENVIRONMENT_OVERRIDE_BYTES = 1024 * 1024


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
    model: str | None = None
    effort: AgyEffort | None = None
    environment_overrides: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    shutdown_callback: Callable[[], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    max_line_bytes: int = 4 * 1024 * 1024
    max_stderr_bytes: int = 64 * 1024
    max_pending_events: int = 2
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
        if self.model is not None and (
            not isinstance(self.model, str)
            or not self.model
            or "\x00" in self.model
            or len(self.model.encode("utf-8")) > 128
        ):
            raise ValueError("model is invalid")
        if self.effort is not None and self.effort not in AGY_EFFORTS:
            raise ValueError("effort is invalid")
        if self.model is not None and self.effort is not None:
            raise ValueError("model and effort are mutually exclusive")
        if not isinstance(self.environment_overrides, tuple):
            raise ValueError("environment_overrides must be a tuple")
        environment_names: set[str] = set()
        environment_bytes = 0
        for entry in self.environment_overrides:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise ValueError("environment override entries must be pairs")
            name, value = entry
            if (
                not isinstance(name, str)
                or not name
                or "=" in name
                or "\x00" in name
                or name in environment_names
                or not isinstance(value, str)
                or "\x00" in value
            ):
                raise ValueError("environment override is invalid")
            try:
                environment_bytes += len(name.encode("utf-8")) + len(value.encode("utf-8"))
            except UnicodeError:
                raise ValueError("environment override is invalid") from None
            environment_names.add(name)
        if environment_bytes > _MAX_ENVIRONMENT_OVERRIDE_BYTES:
            raise ValueError("environment overrides exceed the byte limit")
        if self.shutdown_callback is not None and not callable(self.shutdown_callback):
            raise ValueError("shutdown_callback must be callable")
        _positive_integer("max_line_bytes", self.max_line_bytes)
        _positive_integer("max_stderr_bytes", self.max_stderr_bytes)
        _positive_integer("max_pending_events", self.max_pending_events)
        if self.max_line_bytes * self.max_pending_events > _MAX_PENDING_EVENT_BYTES:
            raise ValueError("combined event buffer limits are unsafe")
        _positive_seconds("init_timeout", self.init_timeout)
        _positive_seconds("write_timeout", self.write_timeout)
        _positive_seconds("cancel_grace", self.cancel_grace)
        _positive_seconds("kill_grace", self.kill_grace)
