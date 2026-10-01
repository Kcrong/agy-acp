from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

MODE = sys.argv[1]
MARKER_ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else None
CONVERSATION_ID = "fake-session"


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


def result(status: str = "SUCCESS") -> dict[str, object]:
    return {
        "event": "result",
        "result": {
            "conversation_id": CONVERSATION_ID,
            "status": status,
            "response": "fake-response" if status == "SUCCESS" else "",
            "error": None,
            "duration_seconds": 0,
            "num_turns": 1,
            "usage": {},
        },
    }


if MODE == "no-init":
    raise SystemExit(7)

if MODE in {"ignore-term", "descendant", "idle-exit-descendant"}:
    signal.signal(signal.SIGTERM, ignore_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, ignore_signal)

if MODE == "stderr":
    os.write(sys.stderr.fileno(), b"x" * 4096)

if MODE in {"descendant", "idle-exit-descendant"}:
    if MARKER_ROOT is None:
        raise SystemExit(9)
    started = MARKER_ROOT / "descendant-started"
    survived = MARKER_ROOT / "descendant-survived"
    code = (
        "import time; from pathlib import Path; "
        f"Path({str(started)!r}).write_text('started', encoding='utf-8'); "
        f"time.sleep(1.2); Path({str(survived)!r}).write_text('alive', encoding='utf-8')"
    )
    subprocess.Popen([sys.executable, "-c", code])
    if MODE == "idle-exit-descendant":
        deadline = time.monotonic() + 2
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not started.exists():
            raise SystemExit(10)

emit(
    {
        "event": "init",
        "conversation_id": CONVERSATION_ID,
        "init": {
            "cwd": os.getcwd(),
            "permission_mode": "request-review",
            "tools": [],
        },
    }
)

if MODE == "idle-exit-descendant":
    raise SystemExit(0)

for line in sys.stdin:
    payload = json.loads(line)
    if payload.get("event") != "user":
        continue
    if MODE == "malformed":
        os.write(sys.stdout.fileno(), b"{broken}\n")
        time.sleep(30)
        continue
    if MODE == "early-exit":
        raise SystemExit(7)
    if MODE == "unknown":
        emit({"event": "future_event", "secret": "must-not-survive"})
    emit(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": CONVERSATION_ID,
                "step_index": 1,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "fake-response",
            },
        }
    )
    if MODE in {"hang", "ignore-term", "descendant"}:
        time.sleep(30)
        continue
    emit(result())
