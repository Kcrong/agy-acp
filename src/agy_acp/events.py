from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from agy_acp.errors import InvalidEventError


class AgyResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    CANCELED = "CANCELED"
    INTERRUPTED = "INTERRUPTED"
    INVALID = "INVALID"
    WAITING = "WAITING"
    RUNNING = "RUNNING"


@dataclass(frozen=True, slots=True)
class AgyInitEvent:
    conversation_id: str
    cwd: str
    permission_mode: str
    tool_count: int
    model: str | None
    agent: str | None


@dataclass(frozen=True, slots=True)
class AgyStepUpdateEvent:
    conversation_id: str
    step_index: int
    state: str
    step_type: str
    text_delta: str | None
    duration_seconds: float | None
    has_usage: bool


@dataclass(frozen=True, slots=True)
class AgyResultEvent:
    conversation_id: str
    status: AgyResultStatus
    response: str
    error: str | None
    duration_seconds: float
    num_turns: int
    has_usage: bool


@dataclass(frozen=True, slots=True)
class AgyUnknownEvent:
    pass


AgyEvent = AgyInitEvent | AgyStepUpdateEvent | AgyResultEvent | AgyUnknownEvent


class _EventShapeError(ValueError):
    pass


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _EventShapeError
    return value


def _required_string(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _EventShapeError
    return value


def _required_text(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str):
        raise _EventShapeError
    return value


def _optional_string(values: Mapping[str, object], key: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _EventShapeError
    return value


def _required_integer(values: Mapping[str, object], key: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _EventShapeError
    return value


def _required_number(values: Mapping[str, object], key: str) -> float:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _EventShapeError
    try:
        number = float(value)
    except OverflowError:
        raise _EventShapeError from None
    if not math.isfinite(number) or number < 0:
        raise _EventShapeError
    return number


def _optional_number(values: Mapping[str, object], key: str) -> float | None:
    value = values.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _EventShapeError
    try:
        number = float(value)
    except OverflowError:
        raise _EventShapeError from None
    if not math.isfinite(number) or number < 0:
        raise _EventShapeError
    return number


def _usage_presence(values: Mapping[str, object]) -> bool:
    if "usage" not in values:
        return False
    if not isinstance(values["usage"], Mapping):
        raise _EventShapeError
    return True


def _parse_init(payload: Mapping[str, object]) -> AgyInitEvent:
    values = _mapping(payload.get("init"))
    tools_value = values.get("tools")
    if not isinstance(tools_value, list) or any(
        not (isinstance(tool, Mapping) or (isinstance(tool, str) and tool)) for tool in tools_value
    ):
        raise _EventShapeError
    return AgyInitEvent(
        conversation_id=_required_string(payload, "conversation_id"),
        cwd=_required_string(values, "cwd"),
        permission_mode=_required_string(values, "permission_mode"),
        tool_count=len(tools_value),
        model=_optional_string(values, "model"),
        agent=_optional_string(values, "agent"),
    )


def _parse_step_update(payload: Mapping[str, object]) -> AgyStepUpdateEvent:
    values = _mapping(payload.get("step_update"))
    state = _required_string(values, "state")
    return AgyStepUpdateEvent(
        conversation_id=_required_string(values, "conversation_id"),
        step_index=_required_integer(values, "step_index"),
        state=state,
        step_type=_required_string(values, "step_type"),
        text_delta=_optional_string(values, "text_delta"),
        duration_seconds=_optional_number(values, "duration_seconds"),
        has_usage=_usage_presence(values),
    )


def _parse_result(payload: Mapping[str, object]) -> AgyResultEvent:
    values = _mapping(payload.get("result"))
    try:
        status = AgyResultStatus(_required_string(values, "status"))
    except ValueError:
        raise _EventShapeError from None
    error = _optional_string(values, "error")
    if status is AgyResultStatus.SUCCESS and error:
        raise _EventShapeError
    return AgyResultEvent(
        conversation_id=_required_string(values, "conversation_id"),
        status=status,
        response=_required_text(values, "response"),
        error=error,
        duration_seconds=_required_number(values, "duration_seconds"),
        num_turns=_required_integer(values, "num_turns"),
        has_usage=_usage_presence(values),
    )


def parse_agy_event(payload: Mapping[str, object]) -> AgyEvent:
    try:
        event = _required_string(payload, "event")
        if event == "init":
            return _parse_init(payload)
        if event == "step_update":
            return _parse_step_update(payload)
        if event == "result":
            return _parse_result(payload)
        return AgyUnknownEvent()
    except _EventShapeError:
        raise InvalidEventError from None
