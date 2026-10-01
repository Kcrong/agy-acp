from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import venv
from collections.abc import Sequence
from pathlib import Path

_REPOSITORY = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git")
_REVISION = re.compile(r"[0-9a-f]{40}")
_TIMEOUT_SECONDS = 180
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


def _run(stage: str, command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{stage} timed out") from error
    if completed.returncode != 0:
        raise RuntimeError(f"{stage} failed with exit code {completed.returncode}")
    return completed.stdout.strip()


def run_smoke(repository: str, revision: str) -> None:
    requirement = build_requirement(repository, revision)
    with tempfile.TemporaryDirectory(
        prefix="agy-acp-git-install-",
        dir=_scratch_root(),
    ) as temporary_directory:
        environment = Path(temporary_directory) / "venv"
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
                "--no-input",
                requirement,
            ],
        )
        imported_version = _run(
            "Package import",
            [
                str(python),
                "-c",
                "import agy_acp; print(agy_acp.__version__)",
            ],
        )
        if imported_version != _EXPECTED_VERSION:
            raise RuntimeError("Package import returned an unexpected version")
        executable = _console_script(environment)
        if _run("Console version", [str(executable), "--version"]) != (
            f"agy-acp {_EXPECTED_VERSION}"
        ):
            raise RuntimeError("Console script returned an unexpected version")
        help_text = _run("Console help", [str(executable), "--help"])
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
