from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from typing import BinaryIO


def _copy_input(source: BinaryIO, destination: BinaryIO) -> None:
    try:
        while chunk := os.read(source.fileno(), 64 * 1024):
            destination.write(chunk)
            destination.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError):
            destination.close()


def _copy_output(source: BinaryIO, destination: BinaryIO) -> None:
    try:
        while chunk := os.read(source.fileno(), 64 * 1024):
            destination.write(chunk)
            destination.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError):
            source.close()


def _read_control_line() -> bytes:
    control = bytearray()
    while len(control) <= 1024 * 1024:
        byte = os.read(sys.stdin.fileno(), 1)
        if not byte:
            raise EOFError
        if byte == b"\n":
            return bytes(control)
        control.extend(byte)
    raise ValueError("bootstrap command exceeds limit")


def main() -> int:
    try:
        control = _read_control_line()
        command = json.loads(control)
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(value, str) or "\x00" in value for value in command)
        ):
            return 2
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            return 2
        threads = [
            threading.Thread(
                target=_copy_input,
                args=(sys.stdin.buffer, process.stdin),
                daemon=True,
            ),
            threading.Thread(
                target=_copy_output,
                args=(process.stdout, sys.stdout.buffer),
            ),
            threading.Thread(
                target=_copy_output,
                args=(process.stderr, sys.stderr.buffer),
            ),
        ]
        for thread in threads:
            thread.start()
        returncode = process.wait()
        for thread in threads[1:]:
            thread.join()
        return returncode
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
