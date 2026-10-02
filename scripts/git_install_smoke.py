from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, BinaryIO, ClassVar

_REPOSITORY = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git")
_REVISION = re.compile(r"[0-9a-f]{40}")
_TIMEOUT_SECONDS = 180.0
_CLEANUP_TIMEOUT_SECONDS = 10.0
_MAX_OUTPUT_BYTES = 64 * 1024
_WINDOWS_KILL_ON_JOB_CLOSE = 0x00002000
_WINDOWS_EXTENDED_LIMIT_INFORMATION = 9
_BOOTSTRAP = """
import json
import subprocess
import sys

command = json.loads(sys.stdin.read())
raise SystemExit(subprocess.run(command, check=False).returncode)
"""


def build_requirement(repository: str, revision: str) -> str:
    if _REPOSITORY.fullmatch(repository) is None:
        raise ValueError("invalid public GitHub repository URL")
    if _REVISION.fullmatch(revision) is None:
        raise ValueError("invalid exact Git revision")
    return f"git+{repository}@{revision}"


def _project_version() -> str:
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as stream:
        project = tomllib.load(stream).get("project")
    if not isinstance(project, dict):
        raise RuntimeError("Project metadata is missing")
    value = project.get("version")
    if not isinstance(value, str) or not value:
        raise RuntimeError("Project version is invalid")
    return value


def _scratch_root() -> Path:
    for name in ("KIROCREW_SCRATCH", "RUNNER_TEMP"):
        value = os.environ.get(name)
        if value:
            root = Path(value)
            if root.is_dir():
                return root
            raise RuntimeError(f"{name} does not name an existing directory")
    return Path(tempfile.gettempdir())


def _environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _console_script(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "agy-acp.exe"
    return environment / "bin" / "agy-acp"


def _safe_path() -> str:
    entries: list[str] = []
    for executable in (sys.executable, shutil.which("git")):
        if executable:
            entry = str(Path(executable).resolve().parent)
            if entry not in entries:
                entries.append(entry)
    for entry in os.defpath.split(os.pathsep):
        if entry and entry != "." and entry not in entries:
            entries.append(entry)
    return os.pathsep.join(entries)


def _safe_environment(root: Path) -> dict[str, str]:
    home = root / "home"
    cache = root / "cache"
    temporary = root / "tmp"
    for directory in (home, cache, temporary):
        directory.mkdir(parents=True, exist_ok=True)

    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(home),
        "NO_COLOR": "1",
        "PATH": _safe_path(),
        "PIP_CACHE_DIR": str(cache / "pip"),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_CACHE_DIR": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "TMPDIR": str(temporary),
        "USERPROFILE": str(home),
        "XDG_CACHE_HOME": str(cache),
    }
    for name in ("COMSPEC", "LANG", "LC_ALL", "PATHEXT", "SYSTEMROOT", "WINDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


class _BoundedCapture:
    def __init__(self) -> None:
        self.data = bytearray()
        self.total_bytes = 0
        self.overflow = threading.Event()

    def drain(self, stream: BinaryIO) -> None:
        try:
            while chunk := os.read(stream.fileno(), 8192):
                self.total_bytes += len(chunk)
                remaining = _MAX_OUTPUT_BYTES + 1 - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if self.total_bytes > _MAX_OUTPUT_BYTES:
                    self.overflow.set()
        finally:
            stream.close()


def _attach_windows_job(process: subprocess.Popen[bytes]) -> Callable[[], None]:
    ctypes: Any = importlib.import_module("ctypes")
    wintypes: Any = importlib.import_module("ctypes.wintypes")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class BasicLimitInformation(ctypes.Structure):  # type: ignore[misc]
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):  # type: ignore[misc]
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):  # type: ignore[misc]
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    create_job = kernel32.CreateJobObjectW
    create_job.restype = wintypes.HANDLE
    set_information = kernel32.SetInformationJobObject
    assign_process = kernel32.AssignProcessToJobObject
    close_handle = kernel32.CloseHandle

    handle = create_job(None, None)
    if not handle:
        raise RuntimeError("Process containment could not be created")
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = _WINDOWS_KILL_ON_JOB_CLOSE
    if not set_information(
        handle,
        _WINDOWS_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        close_handle(handle)
        raise RuntimeError("Process containment could not be configured")
    process_handle = getattr(process, "_handle", None)
    if process_handle is None or not assign_process(handle, int(process_handle)):
        close_handle(handle)
        raise RuntimeError("Process containment could not attach the child")

    closed = False

    def close_job() -> None:
        nonlocal closed
        if not closed:
            closed = True
            close_handle(handle)

    return close_job


def _close_process_tree(
    process: subprocess.Popen[bytes],
    close_job: Callable[[], None] | None,
) -> None:
    if close_job is not None:
        close_job()
    else:
        kill_group: Any = getattr(os, "killpg", None)
        kill_signal: Any = getattr(signal, "SIGKILL", None)
        if kill_group is not None and kill_signal is not None:
            with contextlib.suppress(ProcessLookupError):
                kill_group(process.pid, kill_signal)
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_CLEANUP_TIMEOUT_SECONDS)


def _spawn_contained(
    command: Sequence[str],
    *,
    root: Path,
    environment: dict[str, str],
) -> tuple[subprocess.Popen[bytes], Callable[[], None] | None]:
    creation_flags = 0
    if os.name == "nt":
        creation_flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    process = subprocess.Popen(
        [sys.executable, "-c", _BOOTSTRAP],
        cwd=root,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name != "nt",
        creationflags=creation_flags,
    )
    close_job: Callable[[], None] | None = None
    try:
        if os.name == "nt":
            close_job = _attach_windows_job(process)
        if process.stdin is None:
            raise RuntimeError("Process bootstrap input is unavailable")
        process.stdin.write(json.dumps(list(command)).encode("utf-8"))
        process.stdin.close()
    except BaseException:
        _close_process_tree(process, close_job)
        raise
    return process, close_job


def _run(
    stage: str,
    command: Sequence[str],
    *,
    root: Path,
    timeout_seconds: float = _TIMEOUT_SECONDS,
) -> str:
    environment = _safe_environment(root)
    try:
        process, close_job = _spawn_contained(command, root=root, environment=environment)
    except (OSError, RuntimeError, ValueError) as error:
        raise RuntimeError(f"{stage} could not start") from error
    if process.stdout is None or process.stderr is None:
        _close_process_tree(process, close_job)
        raise RuntimeError(f"{stage} could not capture output")

    stdout = _BoundedCapture()
    stderr = _BoundedCapture()
    readers = [
        threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout_seconds
    outcome = "completed"
    try:
        while process.poll() is None:
            if stdout.overflow.is_set() or stderr.overflow.is_set():
                outcome = "overflow"
                break
            if time.monotonic() >= deadline:
                outcome = "timeout"
                break
            time.sleep(0.01)
        return_code = process.poll()
    finally:
        _close_process_tree(process, close_job)
        for reader in readers:
            reader.join(timeout=_CLEANUP_TIMEOUT_SECONDS)

    if any(reader.is_alive() for reader in readers):
        raise RuntimeError(f"{stage} output cleanup timed out")
    if stdout.overflow.is_set() or stderr.overflow.is_set():
        raise RuntimeError(f"{stage} output exceeded the {_MAX_OUTPUT_BYTES}-byte limit")
    if outcome == "timeout":
        raise RuntimeError(
            f"{stage} timed out; stdout_bytes={stdout.total_bytes}; "
            f"stderr_bytes={stderr.total_bytes}"
        )
    if outcome == "overflow":
        raise RuntimeError(f"{stage} output exceeded the {_MAX_OUTPUT_BYTES}-byte limit")
    if return_code != 0:
        raise RuntimeError(
            f"{stage} failed with exit code {return_code}; "
            f"stdout_bytes={stdout.total_bytes}; stderr_bytes={stderr.total_bytes}"
        )
    return bytes(stdout.data).decode("utf-8", errors="strict").strip()


def run_smoke(repository: str, revision: str) -> None:
    requirement = build_requirement(repository, revision)
    expected_version = _project_version()
    with tempfile.TemporaryDirectory(
        prefix="agy-acp-git-install-",
        dir=_scratch_root(),
    ) as temporary_directory:
        root = Path(temporary_directory)
        environment = root / "venv"
        _run(
            "Environment setup",
            [sys.executable, "-m", "venv", str(environment)],
            root=root,
        )
        python = _environment_python(environment)
        _run(
            "Git installation",
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--no-input",
                "--quiet",
                requirement,
            ],
            root=root,
        )
        imported_version = _run(
            "Package import",
            [
                str(python),
                "-c",
                "import agy_acp; print(agy_acp.__version__)",
            ],
            root=root,
        )
        if imported_version != expected_version:
            raise RuntimeError("Package import returned an unexpected version")
        executable = _console_script(environment)
        if _run("Console version", [str(executable), "--version"], root=root) != (
            f"agy-acp {expected_version}"
        ):
            raise RuntimeError("Console script returned an unexpected version")
        help_text = _run("Console help", [str(executable), "--help"], root=root)
        if not help_text.startswith("usage: agy-acp"):
            raise RuntimeError("Console help returned an unexpected format")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an exact public Git revision in an isolated environment."
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        run_smoke(arguments.repository, arguments.revision)
    except (OSError, RuntimeError, ValueError):
        print("git install smoke: failed", file=sys.stderr)
        return 1
    print("git install smoke: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
