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


def test_run_bounds_captured_output(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="output exceeded"):
        _run(
            "Output probe",
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 70000)"],
            root=tmp_path,
        )


def test_run_reports_bounded_failure_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="safe-diagnostic-marker"):
        _run(
            "Failure probe",
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('safe-diagnostic-marker'); raise SystemExit(7)",
            ],
            root=tmp_path,
        )


def test_run_timeout_terminates_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-survived"
    descendant = (
        "import time; from pathlib import Path; "
        f"time.sleep(0.8); Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); time.sleep(30)"
    )

    with pytest.raises(RuntimeError, match="timed out"):
        _run(
            "Timeout probe",
            [sys.executable, "-c", parent],
            root=tmp_path,
            timeout_seconds=0.2,
        )

    time.sleep(1.0)
    assert not marker.exists()


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
