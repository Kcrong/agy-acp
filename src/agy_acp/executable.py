from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agy_acp.errors import ExecutableResolutionError


@dataclass(frozen=True, slots=True)
class AgyCommand:
    executable: Path
    prefix_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.executable.is_absolute():
            raise ValueError("executable must be absolute")
        if any("\x00" in argument for argument in self.prefix_args):
            raise ValueError("prefix arguments must not contain NUL")


def _executable_names(raw: str) -> tuple[str, ...]:
    if os.name != "nt" or Path(raw).suffix:
        return (raw,)
    extensions = tuple(
        extension
        for extension in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
        if extension.startswith(".")
        and "/" not in extension
        and "\\" not in extension
        and "\x00" not in extension
    )
    return tuple(raw + extension for extension in extensions)


def _find_bare_executable(raw: str, source_path: str) -> Path | None:
    names = _executable_names(raw)
    for entry in source_path.split(os.pathsep):
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        for name in names:
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def resolve_executable(value: str | os.PathLike[str], *, path: str | None = None) -> Path:
    raw = os.fspath(value)
    if not raw or "\x00" in raw:
        raise ExecutableResolutionError("Executable could not be resolved")
    has_separator = "/" in raw or "\\" in raw
    candidate = Path(raw)
    if has_separator and not candidate.is_absolute():
        raise ExecutableResolutionError("Executable must be an absolute path or bare command")

    resolved: Path | None
    if candidate.is_absolute():
        resolved = candidate
    else:
        source_path = os.environ.get("PATH", "") if path is None else path
        resolved = _find_bare_executable(raw, source_path)

    try:
        if resolved is None:
            raise FileNotFoundError
        canonical = resolved.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise ExecutableResolutionError("Executable could not be resolved") from None
    if not canonical.is_file() or not os.access(canonical, os.X_OK):
        raise ExecutableResolutionError("Executable could not be resolved")
    return canonical


def build_agy_argv(
    command: AgyCommand,
    *,
    conversation_id: str | None = None,
    additional_directories: tuple[Path, ...] = (),
) -> tuple[str, ...]:
    argv = [
        str(command.executable),
        *command.prefix_args,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
    ]
    if conversation_id is not None:
        if not conversation_id or "\x00" in conversation_id:
            raise ValueError("conversation_id must be nonempty and contain no NUL")
        argv.extend(("--conversation", conversation_id))
    for directory in additional_directories:
        if not directory.is_absolute():
            raise ValueError("additional directories must be absolute")
        raw = str(directory)
        if "\x00" in raw:
            raise ValueError("additional directories must contain no NUL")
        argv.extend(("--add-dir", raw))
    return tuple(argv)
