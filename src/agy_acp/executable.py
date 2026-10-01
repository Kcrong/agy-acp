from __future__ import annotations

import os
import shutil
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
        absolute_entries = [
            entry for entry in source_path.split(os.pathsep) if entry and Path(entry).is_absolute()
        ]
        found = shutil.which(raw, path=os.pathsep.join(absolute_entries))
        resolved = Path(found) if found else None

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
