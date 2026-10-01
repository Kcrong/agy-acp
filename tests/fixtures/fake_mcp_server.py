import hashlib
import json
import os
import sys
import time
from pathlib import Path

MCP_ENV_PREFIX = "AGY_ACP_MCP_SPEC_"
MARKER = Path(sys.argv[1])
EXPECTED_NAME = sys.argv[2]
EXPECTED_DIGEST = sys.argv[3]
FORBIDDEN_NAME = sys.argv[4]
EXPECTED_CWD_DIGEST = sys.argv[5]
SURVIVOR = Path(sys.argv[6]) if len(sys.argv) > 6 else None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def send(payload: dict[str, object]) -> None:
    os.write(
        sys.stdout.fileno(),
        (json.dumps(payload, separators=(",", ":")) + "\n").encode(),
    )


MARKER.write_text(
    json.dumps(
        {
            "expected_env": digest(os.environ.get(EXPECTED_NAME, "")) == EXPECTED_DIGEST,
            "forbidden_env": FORBIDDEN_NAME not in os.environ,
            "internal_specs_absent": not any(
                name.startswith(MCP_ENV_PREFIX) for name in os.environ
            ),
            "python_controls_absent": not any(
                name in os.environ for name in ("PYTHONHOME", "PYTHONPATH")
            ),
            "cwd_preserved": digest(os.getcwd()) == EXPECTED_CWD_DIGEST,
        },
        separators=(",", ":"),
    ),
    encoding="utf-8",
)

for line in sys.stdin:
    request = json.loads(line)
    request_id = request.get("id")
    method = request.get("method")
    if method == "initialize":
        result: dict[str, object] = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fixture", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": []}
    else:
        result = {}
    send({"jsonrpc": "2.0", "id": request_id, "result": result})
    if method == "tools/list" and SURVIVOR is not None:
        time.sleep(1.2)
        SURVIVOR.write_text("survived", encoding="utf-8")
        time.sleep(30)
