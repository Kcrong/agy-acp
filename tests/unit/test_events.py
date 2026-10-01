from __future__ import annotations

import pytest

from agy_acp.errors import InvalidEventError
from agy_acp.events import (
    AgyInitEvent,
    AgyResultEvent,
    AgyResultStatus,
    AgyStepUpdateEvent,
    AgyUnknownEvent,
    parse_agy_event,
)


def test_init_event_is_typed_and_immutable() -> None:
    event = parse_agy_event(
        {
            "event": "init",
            "conversation_id": "opaque-session",
            "init": {
                "cwd": "/workspace",
                "permission_mode": "request-review",
                "tools": ["read", {"name": "write", "secret": "credential-sentinel"}],
            },
        }
    )

    assert event == AgyInitEvent(
        conversation_id="opaque-session",
        cwd="/workspace",
        permission_mode="request-review",
        tool_count=2,
        model=None,
        agent=None,
    )
    assert "credential-sentinel" not in repr(event)
    with pytest.raises(AttributeError):
        event.cwd = "/other"  # type: ignore[misc]


def test_init_optional_model_and_agent_are_typed() -> None:
    event = parse_agy_event(
        {
            "event": "init",
            "conversation_id": "opaque-session",
            "init": {
                "cwd": "/workspace",
                "permission_mode": "request-review",
                "tools": [],
                "model": "model-name",
                "agent": "agent-name",
                "future_field": True,
            },
        }
    )

    assert isinstance(event, AgyInitEvent)
    assert event.model == "model-name"
    assert event.agent == "agent-name"


def test_text_step_update_is_typed() -> None:
    event = parse_agy_event(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "opaque-session",
                "step_index": 2,
                "state": "running",
                "step_type": "agent_response",
                "text_delta": "hello",
                "duration_seconds": 0.5,
                "usage": {"total_tokens": 4},
            },
        }
    )

    assert event == AgyStepUpdateEvent(
        conversation_id="opaque-session",
        step_index=2,
        state="running",
        step_type="agent_response",
        text_delta="hello",
        duration_seconds=0.5,
        has_usage=True,
    )


@pytest.mark.parametrize("status", list(AgyResultStatus))
def test_every_documented_result_status_is_typed(status: AgyResultStatus) -> None:
    event = parse_agy_event(
        {
            "event": "result",
            "result": {
                "conversation_id": "opaque-session",
                "status": status.value,
                "response": "response",
                "error": None,
                "duration_seconds": 1,
                "num_turns": 2,
                "usage": {},
            },
        }
    )

    assert event == AgyResultEvent(
        conversation_id="opaque-session",
        status=status,
        response="response",
        error=None,
        duration_seconds=1.0,
        num_turns=2,
        has_usage=True,
    )


def test_success_with_nonempty_error_is_contradictory() -> None:
    with pytest.raises(InvalidEventError, match="Invalid agy event"):
        parse_agy_event(
            {
                "event": "result",
                "result": {
                    "conversation_id": "opaque-session",
                    "status": "SUCCESS",
                    "response": "response",
                    "error": "contradiction",
                    "duration_seconds": 1,
                    "num_turns": 1,
                    "usage": {},
                },
            }
        )


def test_unknown_event_discards_name_and_payload() -> None:
    secret = "credential-sentinel"
    event = parse_agy_event({"event": secret, "payload": secret})

    assert event == AgyUnknownEvent()
    assert secret not in repr(event)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"event": 3},
        {"event": "init", "conversation_id": "id", "init": {"tools": []}},
        {
            "event": "init",
            "conversation_id": "id",
            "init": {"cwd": "/x", "permission_mode": "p", "tools": [1]},
        },
        {
            "event": "init",
            "conversation_id": "id",
            "init": {"cwd": "safe\x00tail", "permission_mode": "p", "tools": []},
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "id",
                "step_index": True,
                "state": "ACTIVE",
                "step_type": "agent_response",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "id",
                "step_index": 0,
                "state": "",
                "step_type": "agent_response",
            },
        },
        {
            "event": "result",
            "result": {
                "conversation_id": "id",
                "status": "FUTURE",
                "response": "",
                "duration_seconds": 0,
                "num_turns": 0,
                "usage": {},
            },
        },
    ],
)
def test_invalid_known_event_shape_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(InvalidEventError, match="Invalid agy event"):
        parse_agy_event(payload)


def test_invalid_event_error_never_echoes_payload() -> None:
    secret = "credential-sentinel"
    with pytest.raises(InvalidEventError) as raised:
        parse_agy_event(
            {
                "event": "result",
                "result": {
                    "conversation_id": secret,
                    "status": "SUCCESS",
                    "response": secret,
                },
            }
        )

    assert secret not in str(raised.value)


@pytest.mark.parametrize("response", [None, 0, False, []])
def test_result_requires_a_string_response(response: object) -> None:
    with pytest.raises(InvalidEventError):
        parse_agy_event(
            {
                "event": "result",
                "result": {
                    "conversation_id": "id",
                    "status": "ERROR",
                    "response": response,
                    "duration_seconds": 0,
                    "num_turns": 0,
                    "usage": {},
                },
            }
        )


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
def test_event_duration_must_be_finite(duration: float) -> None:
    with pytest.raises(InvalidEventError):
        parse_agy_event(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "id",
                    "step_index": 0,
                    "state": "DONE",
                    "step_type": "checkpoint",
                    "duration_seconds": duration,
                },
            }
        )


def test_huge_event_duration_is_rejected_without_overflow() -> None:
    with pytest.raises(InvalidEventError, match="Invalid agy event"):
        parse_agy_event(
            {
                "event": "result",
                "result": {
                    "conversation_id": "id",
                    "status": "ERROR",
                    "response": "",
                    "duration_seconds": 10**1000,
                    "num_turns": 0,
                    "usage": {},
                },
            }
        )
