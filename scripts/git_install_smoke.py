from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import venv
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

_REPOSITORY = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git")
_REVISION = re.compile(r"[0-9a-f]{40}")
_TIMEOUT_SECONDS = 180.0
_CLEANUP_TIMEOUT_SECONDS = 10.0
_MAX_OUTPUT_BYTES = 64 * 1024
_DIAGNOSTIC_BYTES = 4 * 1024
_EXPECTED_VERSION = "0.1.0"


def build_requirement(repository: str, revision: str) -> str:
    if _REPOSITORY.fullmatch(repository) is None:
        raise ValueError("invalid public GitHub repository URL")
    if _REVISION.fullmatch(revision) is None:
        raise ValueError("invalid exact Git revision")
    return f"git+{repository}@{revision}"


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


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    environment: dict[str, str],
) -> None:
    if os.name == "nt":
        system_root = environment.get("SYSTEMROOT", r"C:\Windows")
        taskkill = Path(system_root) / "System32" / "taskkill.exe"
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_CLEANUP_TIMEOUT_SECONDS,
                env=environment,
            )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)

    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_CLEANUP_TIMEOUT_SECONDS)


def _read_bytes(stream: BinaryIO, limit: int, *, tail: bool = False) -> bytes:
    stream.flush()
    size = stream.tell()
    if tail:
        stream.seek(max(0, size - limit))
    else:
        stream.seek(0)
    return stream.read(limit + 1)


def _sanitize_diagnostic(data: bytes, root: Path) -> str:
    text = data.decode("utf-8", errors="replace")
    text = text.replace(str(root), "<scratch>")
    text = text.replace(str(Path.home()), "<home>")
    text = re.sub(r"https://[^\s/@:]+:[^\s/@]+@", "https://<redacted>@", text)
    text = re.sub(
        r"(?i)\b(authorization|password|secret|token)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text,
    )
    return " ".join(text.split())


def _run(
    stage: str,
    command: Sequence[str],
    *,
    root: Path,
    timeout_seconds: float = _TIMEOUT_SECONDS,
) -> str:
    environment = _safe_environment(root)
    creation_flags = 0
    if os.name == "nt":
        creation_flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    with tempfile.TemporaryFile(dir=root) as stdout, tempfile.TemporaryFile(dir=root) as stderr:
        process = subprocess.Popen(
            list(command),
            cwd=root,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            start_new_session=os.name != "nt",
            creationflags=creation_flags,
        )
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            _terminate_process_tree(process, environment)
            diagnostic = _sanitize_diagnostic(
                _read_bytes(stderr, _DIAGNOSTIC_BYTES, tail=True),
                root,
            )
            suffix = f": {diagnostic}" if diagnostic else ""
            raise RuntimeError(f"{stage} timed out{suffix}") from error

        stdout_data = _read_bytes(stdout, _MAX_OUTPUT_BYTES)
        stderr_data = _read_bytes(stderr, _MAX_OUTPUT_BYTES)
        if len(stdout_data) > _MAX_OUTPUT_BYTES or len(stderr_data) > _MAX_OUTPUT_BYTES:
            raise RuntimeError(f"{stage} output exceeded the {_MAX_OUTPUT_BYTES}-byte limit")
        if return_code != 0:
            diagnostic = _sanitize_diagnostic(
                _read_bytes(stderr, _DIAGNOSTIC_BYTES, tail=True),
                root,
            )
            suffix = f": {diagnostic}" if diagnostic else ""
            raise RuntimeError(f"{stage} failed with exit code {return_code}{suffix}")
        return stdout_data.decode("utf-8", errors="strict").strip()


def run_smoke(repository: str, revision: str) -> None:
    requirement = build_requirement(repository, revision)
    with tempfile.TemporaryDirectory(
        prefix="agy-acp-git-install-",
        dir=_scratch_root(),
    ) as temporary_directory:
        root = Path(temporary_directory)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
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
        if imported_version != _EXPECTED_VERSION:
            raise RuntimeError("Package import returned an unexpected version")
        executable = _console_script(environment)
        if _run("Console version", [str(executable), "--version"], root=root) != (
            f"agy-acp {_EXPECTED_VERSION}"
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
    except (RuntimeError, ValueError) as error:
        print(f"git install smoke: {error}", file=sys.stderr)
        return 1
    print("git install smoke: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
