from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

from scripts.package_smoke import (
    _distribution_files,
    _member_path,
    _run,
    _safe_environment,
    main,
)


@pytest.mark.parametrize("name", ["", "/absolute", "../outside", "a/../outside", "a\\b"])
def test_member_path_rejects_unsafe_archive_names(name: str) -> None:
    with pytest.raises(RuntimeError, match="unsafe path"):
        _member_path(name)


def test_member_path_accepts_normalized_archive_name() -> None:
    assert _member_path("agy_acp/module.py") == PurePosixPath("agy_acp/module.py")


def test_distribution_files_selects_exact_archives(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1-py3-none-any.whl"
    sdist = tmp_path / "example-1.tar.gz"
    wheel.touch()
    sdist.touch()
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")

    assert _distribution_files(tmp_path) == (wheel, sdist)


def test_distribution_files_rejects_duplicates(tmp_path: Path) -> None:
    (tmp_path / "one.whl").touch()
    (tmp_path / "two.whl").touch()
    (tmp_path / "one.tar.gz").touch()

    with pytest.raises(RuntimeError, match="exactly one wheel and one sdist"):
        _distribution_files(tmp_path)


def test_safe_environment_excludes_ambient_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_ACP_SECRET_SENTINEL", "must-not-propagate")

    environment = _safe_environment(tmp_path)

    assert "AGY_ACP_SECRET_SENTINEL" not in environment
    assert Path(environment["HOME"]).is_relative_to(tmp_path)
    assert Path(environment["TMPDIR"]).is_relative_to(tmp_path)


def test_run_failure_is_structural_only(tmp_path: Path) -> None:
    environment = _safe_environment(tmp_path)
    secret = "credential-sentinel-must-not-appear"

    with pytest.raises(RuntimeError) as raised:
        _run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stderr.write({secret!r}); raise SystemExit(7)",
            ],
            root=tmp_path,
            environment=environment,
        )

    message = str(raised.value)
    assert secret not in message
    assert "exit code 7" in message
    assert "stderr_bytes=" in message


def test_main_hides_invalid_distribution_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "private-machine-path"

    assert main([str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "package smoke: failed\n"
    assert str(missing) not in captured.err
