from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.git_install_smoke import (
    _run,
    _safe_environment,
    build_requirement,
)


def test_build_requirement_accepts_public_github_exact_sha() -> None:
    revision = "a" * 40
    assert (
        build_requirement("https://github.com/Kcrong/agy-acp.git", revision)
        == f"git+https://github.com/Kcrong/agy-acp.git@{revision}"
    )


@pytest.mark.parametrize(
    ("repository", "revision"),
    [
        ("https://example.com/Kcrong/agy-acp.git", "a" * 40),
        ("https://token@github.com/Kcrong/agy-acp.git", "a" * 40),
        ("https://github.com/Kcrong/agy-acp", "a" * 40),
        ("https://github.com/Kcrong/agy-acp.git", "main"),
        ("https://github.com/Kcrong/agy-acp.git", "a" * 39),
    ],
)
def test_build_requirement_rejects_non_public_or_non_exact_input(
    repository: str,
    revision: str,
) -> None:
    with pytest.raises(ValueError, match="invalid"):
        build_requirement(repository, revision)


def test_safe_environment_excludes_ambient_secret_and_contains_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_ACP_SECRET_SENTINEL", "must-not-propagate")

    environment = _safe_environment(tmp_path)

    assert "AGY_ACP_SECRET_SENTINEL" not in environment
    assert environment["PIP_NO_CACHE_DIR"] == "1"
    assert Path(environment["PIP_CACHE_DIR"]).is_relative_to(tmp_path)
    assert Path(environment["HOME"]).is_relative_to(tmp_path)
    assert Path(environment["TMPDIR"]).is_relative_to(tmp_path)


def test_run_does_not_inherit_ambient_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_ACP_SECRET_SENTINEL", "must-not-propagate")

    output = _run(
        "Environment probe",
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('AGY_ACP_SECRET_SENTINEL', 'missing'))",
        ],
        root=tmp_path,
    )

    assert output == "missing"


def test_run_bounds_output_while_process_is_running(tmp_path: Path) -> None:
    marker = tmp_path / "output-process-survived"
    code = (
        "import sys, time; from pathlib import Path; "
        "sys.stdout.write('x' * 70000); sys.stdout.flush(); "
        f"time.sleep(1); Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="output exceeded"):
        _run("Output probe", [sys.executable, "-c", code], root=tmp_path)

    assert time.monotonic() - started < 1.0
    assert not marker.exists()


def test_run_failure_diagnostic_is_structural_only(tmp_path: Path) -> None:
    secret = "credential-sentinel-must-not-appear"
    code = f"import sys; sys.stderr.write({secret!r}); raise SystemExit(7)"

    with pytest.raises(RuntimeError) as raised:
        _run("Failure probe", [sys.executable, "-c", code], root=tmp_path)

    message = str(raised.value)
    assert secret not in message
    assert message.startswith("Failure probe failed with exit code 7")
    assert "stderr_bytes=" in message
    assert len(message) < 160


def _descendant_program(started: Path, survived: Path) -> str:
    return (
        "import time; from pathlib import Path; "
        f"Path({str(started)!r}).write_text('started', encoding='utf-8'); "
        f"time.sleep(0.8); Path({str(survived)!r}).write_text('alive', encoding='utf-8')"
    )


def test_run_timeout_terminates_started_descendants(tmp_path: Path) -> None:
    started = tmp_path / "descendant-started"
    survived = tmp_path / "descendant-survived"
    descendant = _descendant_program(started, survived)
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); time.sleep(30)"
    )

    with pytest.raises(RuntimeError, match="timed out"):
        _run(
            "Timeout probe",
            [sys.executable, "-c", parent],
            root=tmp_path,
            timeout_seconds=0.5,
        )

    assert started.exists()
    time.sleep(1.0)
    assert not survived.exists()


def test_run_success_terminates_lingering_descendants(tmp_path: Path) -> None:
    started = tmp_path / "success-descendant-started"
    survived = tmp_path / "success-descendant-survived"
    descendant = _descendant_program(started, survived)
    parent = (
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}])\n"
        "deadline = time.monotonic() + 2\n"
        f"started = Path({str(started)!r})\n"
        "while not started.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
    )

    assert _run("Success probe", [sys.executable, "-c", parent], root=tmp_path) == ""

    assert started.exists()
    time.sleep(1.0)
    assert not survived.exists()


def test_run_spawn_failure_is_path_free(tmp_path: Path) -> None:
    missing = tmp_path / "secret-machine-path" / "missing"

    with pytest.raises(RuntimeError) as raised:
        _run("Spawn probe", [str(missing)], root=tmp_path)

    message = str(raised.value)
    assert message.startswith("Spawn probe failed with exit code 1")
    assert "stderr_bytes=" in message
    assert str(tmp_path) not in message


def test_safe_environment_has_git_on_path(tmp_path: Path) -> None:
    environment = _safe_environment(tmp_path)
    executable = "git.exe" if os.name == "nt" else "git"
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0
