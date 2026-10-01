from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agy_acp.config import AgyProcessConfig
from agy_acp.errors import ExecutableResolutionError
from agy_acp.executable import AgyCommand, build_agy_argv, resolve_executable


def test_resolve_absolute_executable_returns_real_path() -> None:
    assert resolve_executable(sys.executable) == Path(sys.executable).resolve()


def test_resolve_bare_command_from_absolute_path_entries() -> None:
    executable = Path(sys.executable).resolve()
    assert resolve_executable(executable.name, path=str(executable.parent)) == executable


def test_relative_path_entries_are_not_searched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "relative-bin"
    directory.mkdir()
    candidate = directory / "agy-probe"
    candidate.write_text("probe", encoding="utf-8")
    candidate.chmod(0o755)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ExecutableResolutionError, match="could not be resolved"):
        resolve_executable("agy-probe", path="relative-bin")


def test_relative_configured_path_is_rejected() -> None:
    with pytest.raises(ExecutableResolutionError, match="absolute path or bare command"):
        resolve_executable("./agy")


def test_missing_absolute_executable_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ExecutableResolutionError, match="could not be resolved"):
        resolve_executable(str(tmp_path / "missing"))


def test_build_argv_is_literal_and_ordered(tmp_path: Path) -> None:
    command = AgyCommand(Path(sys.executable).resolve(), ("-u", "wrapper.py"))
    extra = tmp_path / "extra"

    argv = build_agy_argv(
        command,
        conversation_id="opaque-id",
        additional_directories=(extra,),
    )

    assert argv == (
        str(Path(sys.executable).resolve()),
        "-u",
        "wrapper.py",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--conversation",
        "opaque-id",
        "--add-dir",
        str(extra),
    )


def test_build_argv_rejects_relative_additional_directory() -> None:
    command = AgyCommand(Path(sys.executable).resolve())
    with pytest.raises(ValueError, match="absolute"):
        build_agy_argv(command, additional_directories=(Path("relative"),))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_line_bytes", 0),
        ("max_line_bytes", True),
        ("max_stderr_bytes", 1.5),
        ("max_pending_events", 0),
        ("init_timeout", float("nan")),
        ("write_timeout", float("inf")),
        ("cancel_grace", 0),
        ("kill_grace", float("inf")),
    ],
)
def test_process_config_requires_positive_finite_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "command": AgyCommand(Path(sys.executable).resolve()),
        "cwd": tmp_path,
        field: value,
    }
    with pytest.raises(ValueError, match="positive"):
        AgyProcessConfig(**values)  # type: ignore[arg-type]


def test_process_config_rejects_unsafe_event_buffer_product(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="combined event buffer limits are unsafe"):
        AgyProcessConfig(
            command=AgyCommand(Path(sys.executable).resolve()),
            cwd=tmp_path,
            max_line_bytes=4 * 1024 * 1024,
            max_pending_events=5,
        )


def test_bare_command_never_searches_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "agy-probe"
    candidate = tmp_path / name
    candidate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    candidate.chmod(0o755)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ExecutableResolutionError, match="could not be resolved"):
        resolve_executable(name, path=str(empty_path))
