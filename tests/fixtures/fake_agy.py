from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

MODE = sys.argv[1]
MARKER_ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 and Path(sys.argv[2]).is_absolute() else None
CONVERSATION_ID = f"fake-{Path.cwd().name}" if MODE == "unique" else "fake-session"
INIT_CONVERSATION_ID = "fake\x00session" if MODE == "nul-conversation" else CONVERSATION_ID


def emit(payload: dict[str, object]) -> None:
    line = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    if MODE == "split":
        middle = max(1, len(line) // 2)
        os.write(sys.stdout.fileno(), line[:middle])
        time.sleep(0.01)
        os.write(sys.stdout.fileno(), line[middle:])
        return
    os.write(sys.stdout.fileno(), line)


def ignore_signal(_signum: int, _frame: object) -> None:
    return None


def result(
    status: str = "SUCCESS",
    *,
    response: str | None = None,
    conversation_id: str = CONVERSATION_ID,
) -> dict[str, object]:
    if response is None:
        response = "fake-response" if status == "SUCCESS" else ""
    return {
        "event": "result",
        "result": {
            "conversation_id": conversation_id,
            "status": status,
            "response": response,
            "error": None,
            "duration_seconds": 0,
            "num_turns": 1,
            "usage": {},
        },
    }


if MODE == "no-init":
    raise SystemExit(7)
if MODE == "slow-init":
    time.sleep(30)
if MODE == "slow-init-marker":
    if MARKER_ROOT is None:
        raise SystemExit(12)
    (MARKER_ROOT / "initial-started").write_text("started", encoding="utf-8")
    time.sleep(30)
if MODE == "restart-slow-init" and "--conversation" in sys.argv[2:]:
    if MARKER_ROOT is not None:
        (MARKER_ROOT / "restart-started").write_text("started", encoding="utf-8")
    time.sleep(30)

if MODE in {"ignore-term", "descendant", "idle-exit-descendant"}:
    signal.signal(signal.SIGTERM, ignore_signal)

if MODE == "stderr":
    os.write(sys.stderr.fileno(), b"x" * 4096)

if MODE in {"descendant", "idle-exit-descendant"}:
    if MARKER_ROOT is None:
        raise SystemExit(9)
    started = MARKER_ROOT / f"descendant-started-{os.getpid()}"
    survived = MARKER_ROOT / f"descendant-survived-{os.getpid()}"
    descendant_command = [
        "/bin/sh",
        "-c",
        'printf "%s" started > "$1"; sleep 1.2; printf "%s" alive > "$2"',
        "agy-descendant",
        str(started),
        str(survived),
    ]
    if MODE == "idle-exit-descendant":
        subprocess.Popen(descendant_command)
    else:
        subprocess.Popen(
            descendant_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    deadline = time.monotonic() + 2
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not started.exists():
        raise SystemExit(10)

if MODE == "record-args":
    if MARKER_ROOT is None:
        raise SystemExit(11)
    arguments = sys.argv[3:]
    additional_directories = [
        arguments[index + 1]
        for index, argument in enumerate(arguments[:-1])
        if argument == "--add-dir"
    ]
    with (MARKER_ROOT / "argv.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(additional_directories, separators=(",", ":")) + "\n")

emit(
    {
        "event": "init",
        "conversation_id": INIT_CONVERSATION_ID,
        "init": {
            "cwd": os.getcwd(),
            "permission_mode": "request-review",
            "tools": [],
        },
    }
)

if MODE == "idle-exit-descendant":
    raise SystemExit(0)
if MODE == "init-exit-error":
    raise SystemExit(7)
if MODE == "init-exit-signal":
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(30)
if MODE == "duplicate-init":
    emit(
        {
            "event": "init",
            "conversation_id": INIT_CONVERSATION_ID,
            "init": {
                "cwd": os.getcwd(),
                "permission_mode": "request-review",
                "tools": [],
            },
        }
    )
if MODE == "malformed-after-init":
    os.write(sys.stdout.fileno(), b"{broken}\n")
if MODE == "close-stdout":
    os.close(sys.stdout.fileno())
    time.sleep(30)
if MODE == "no-read":
    time.sleep(30)
if MODE == "pre-result":
    emit(result())

for line in sys.stdin:
    payload = json.loads(line)
    if payload.get("event") != "user":
        continue
    if MODE == "flood":
        for index in range(100):
            emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": CONVERSATION_ID,
                        "step_index": index,
                        "state": "ACTIVE",
                        "step_type": "agent_response",
                        "text_delta": "x",
                    },
                }
            )
        time.sleep(30)
        continue
    if MODE == "malformed":
        os.write(sys.stdout.fileno(), b"{broken}\n")
        time.sleep(30)
        continue
    if MODE == "early-exit":
        raise SystemExit(7)
    if MODE == "burst-exit-zero":
        for index in range(4):
            emit(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": CONVERSATION_ID,
                        "step_index": index,
                        "state": "running",
                        "step_type": "agent_response",
                        "text_delta": "x",
                    },
                }
            )
        emit(result(response="xxxx"))
        raise SystemExit(0)
    if MODE == "unknown":
        emit({"event": "future_event", "secret": "must-not-survive"})
    if MODE == "no-delta":
        emit(result())
        continue
    if MODE == "suffix":
        text_delta = "fake-"
    elif MODE == "empty-delta":
        text_delta = ""
    else:
        text_delta = "fake-response"
    update_conversation_id = "other-session" if MODE == "mismatch" else CONVERSATION_ID
    emit(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": update_conversation_id,
                "step_index": 1,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": text_delta,
            },
        }
    )
    if MODE in {"hang", "ignore-term", "descendant"}:
        time.sleep(30)
        continue
    if MODE == "conflict":
        emit(result(response="different-response"))
        continue
    if MODE == "result-mismatch":
        emit(result(conversation_id="other-session"))
        continue
    if MODE == "backend-canceled":
        emit(result("CANCELED"))
        continue
    terminal_statuses = {
        "backend-error": "ERROR",
        "backend-interrupted": "INTERRUPTED",
        "backend-invalid": "INVALID",
        "backend-waiting": "WAITING",
        "backend-running": "RUNNING",
    }
    if MODE in terminal_statuses:
        emit(result(terminal_statuses[MODE]))
        continue
    if MODE == "result-error-exit":
        emit(result())
        raise SystemExit(7)
    if MODE == "result-exit-zero":
        emit(result())
        raise SystemExit(0)
    if MODE == "result-sigterm":
        emit(result())
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(30)
    if MODE == "result-sigkill":
        emit(result())
        os.kill(os.getpid(), signal.SIGKILL)
    if MODE == "result-malformed-tail":
        emit(result())
        os.write(sys.stdout.fileno(), b"{broken}\n")
        continue
    if MODE == "duplicate-result":
        emit(result())
        emit(result())
        continue
    if MODE == "delayed-duplicate":
        emit(result())
        time.sleep(0.2)
        emit(result())
        continue
    if MODE == "late-update":
        emit(result())
        emit(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": CONVERSATION_ID,
                    "step_index": 2,
                    "state": "ACTIVE",
                    "step_type": "agent_response",
                    "text_delta": "stale-response",
                },
            }
        )
        continue
    emit(result())
