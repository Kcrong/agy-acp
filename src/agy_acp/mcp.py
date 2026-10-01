from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from agy_acp.errors import ExecutableResolutionError, InvalidMcpConfigError, McpHandoffError
from agy_acp.executable import resolve_executable

MCP_ENV_PREFIX = "AGY_ACP_MCP_SPEC_"
MCP_SCRUBBED_ENVIRONMENT = frozenset({"PYTHONHOME", "PYTHONPATH"})
_OWNER_PREFIX = "agy-acp-mcp-owner-"
_GENERATION_PREFIX = "generation-"
_MAX_SERVERS = 16
_MAX_ARGUMENTS = 128
_MAX_ENVIRONMENT = 128
_MAX_SPEC_BYTES = 64 * 1024
_MAX_TOTAL_SPEC_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    name: str
    command: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


class _DuplicateKey(ValueError):
    pass


def _object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError


def _invalid() -> NoReturn:
    raise InvalidMcpConfigError


def _text(value: object, *, allow_empty: bool = False, max_bytes: int = 64 * 1024) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or "\x00" in value:
        _invalid()
    try:
        size = len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        _invalid()
    if size > max_bytes:
        _invalid()
    return value


def _metadata(value: object) -> None:
    if value is not None and not isinstance(value, Mapping):
        _invalid()


def parse_mcp_servers(values: Sequence[object]) -> tuple[McpServerSpec, ...]:
    if isinstance(values, str | bytes | bytearray) or len(values) > _MAX_SERVERS:
        _invalid()
    specs: list[McpServerSpec] = []
    names: set[str] = set()
    total_bytes = 0
    for raw in values:
        if not isinstance(raw, Mapping) or set(raw) - {"name", "command", "args", "env", "_meta"}:
            _invalid()
        _metadata(raw.get("_meta"))
        name = _text(raw.get("name"), max_bytes=512)
        command = _text(raw.get("command"), max_bytes=4096)
        if name in names:
            _invalid()
        names.add(name)

        raw_args = raw.get("args")
        if not isinstance(raw_args, list) or len(raw_args) > _MAX_ARGUMENTS:
            _invalid()
        args = tuple(_text(value, allow_empty=True) for value in raw_args)

        raw_env = raw.get("env")
        if not isinstance(raw_env, list) or len(raw_env) > _MAX_ENVIRONMENT:
            _invalid()
        environment: list[tuple[str, str]] = []
        environment_names: set[str] = set()
        for raw_variable in raw_env:
            if not isinstance(raw_variable, Mapping) or set(raw_variable) - {
                "name",
                "value",
                "_meta",
            }:
                _invalid()
            _metadata(raw_variable.get("_meta"))
            variable_name = _text(raw_variable.get("name"), max_bytes=512)
            variable_value = _text(
                raw_variable.get("value"),
                allow_empty=True,
                max_bytes=_MAX_SPEC_BYTES,
            )
            if (
                "=" in variable_name
                or variable_name.startswith(MCP_ENV_PREFIX)
                or variable_name in environment_names
            ):
                _invalid()
            environment_names.add(variable_name)
            environment.append((variable_name, variable_value))

        spec = McpServerSpec(name=name, command=command, args=args, env=tuple(environment))
        encoded = encode_launcher_spec(spec)
        spec_bytes = len(encoded.encode("utf-8"))
        if spec_bytes > _MAX_SPEC_BYTES:
            _invalid()
        total_bytes += spec_bytes
        if total_bytes > _MAX_TOTAL_SPEC_BYTES:
            _invalid()
        specs.append(spec)
    return tuple(specs)


def resolve_mcp_servers(
    specs: tuple[McpServerSpec, ...],
) -> tuple[McpServerSpec, ...]:
    resolved: list[McpServerSpec] = []
    for spec in specs:
        environment = dict(spec.env)
        try:
            command = resolve_executable(spec.command, path=environment.get("PATH"))
        except ExecutableResolutionError:
            _invalid()
        resolved.append(
            McpServerSpec(
                name=spec.name,
                command=str(command),
                args=spec.args,
                env=spec.env,
            )
        )
    return tuple(resolved)


def encode_launcher_spec(spec: McpServerSpec) -> str:
    return json.dumps(
        {
            "name": spec.name,
            "command": spec.command,
            "args": list(spec.args),
            "env": [{"name": name, "value": value} for name, value in spec.env],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def decode_launcher_spec(raw: str) -> McpServerSpec:
    if not isinstance(raw, str):
        _invalid()
    try:
        encoded_size = len(raw.encode("utf-8", errors="strict"))
    except UnicodeError:
        _invalid()
    if encoded_size > _MAX_SPEC_BYTES:
        _invalid()
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError, _DuplicateKey):
        _invalid()
    parsed = parse_mcp_servers([value])
    if not Path(parsed[0].command).is_absolute():
        _invalid()
    return parsed[0]


@dataclass(slots=True)
class McpGeneration:
    root: Path
    environment_overrides: tuple[tuple[str, str], ...]
    _manager: McpWorkspaceManager
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._manager._close_generation(self.root)
        self._closed = True


def _safe_temp_parent(parent: Path) -> Path:
    try:
        resolved = parent.resolve(strict=True)
        metadata = resolved.lstat()
    except (OSError, RuntimeError):
        raise McpHandoffError from None
    mode = stat.S_IMODE(metadata.st_mode)
    trusted_owner = metadata.st_uid in {0, os.getuid()}
    private = mode & 0o022 == 0
    sticky = bool(metadata.st_mode & stat.S_ISVTX)
    if not resolved.is_absolute() or not stat.S_ISDIR(metadata.st_mode):
        raise McpHandoffError
    if not trusted_owner or (not private and not sticky):
        raise McpHandoffError
    return resolved


class McpWorkspaceManager:
    def __init__(self, *, temp_parent: Path | None = None) -> None:
        requested_parent = Path(tempfile.gettempdir()) if temp_parent is None else temp_parent
        self._parent = _safe_temp_parent(requested_parent)
        self._scavenge()
        self._lease_fd: int | None = None
        try:
            self._root = Path(tempfile.mkdtemp(prefix=_OWNER_PREFIX, dir=self._parent))
            self._root.chmod(0o700)
            lease = self._root / ".lease"
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            self._lease_fd = os.open(lease, flags, 0o600)
            root_metadata = self._root.lstat()
            lease_metadata = os.fstat(self._lease_fd)
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or root_metadata.st_uid != os.getuid()
                or stat.S_IMODE(root_metadata.st_mode) != 0o700
                or not stat.S_ISREG(lease_metadata.st_mode)
                or lease_metadata.st_uid != os.getuid()
                or stat.S_IMODE(lease_metadata.st_mode) != 0o600
                or lease_metadata.st_nlink != 1
            ):
                raise ValueError
            fcntl.flock(self._lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ValueError):
            if self._lease_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._lease_fd)
                self._lease_fd = None
            root = getattr(self, "_root", None)
            if isinstance(root, Path):
                shutil.rmtree(root, ignore_errors=True)
            raise McpHandoffError from None
        self._active_roots: set[Path] = set()
        self._root_removed = False
        self._closed = False

    @property
    def root(self) -> Path:
        return self._root

    def _scavenge(self) -> None:
        try:
            candidates = list(self._parent.glob(f"{_OWNER_PREFIX}*"))
        except OSError:
            raise McpHandoffError from None
        for candidate in candidates:
            candidate_fd: int | None = None
            lease_fd: int | None = None
            try:
                directory_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                )
                candidate_fd = os.open(candidate, directory_flags)
                candidate_metadata = os.fstat(candidate_fd)
                path_metadata = candidate.lstat()
                if (
                    not stat.S_ISDIR(candidate_metadata.st_mode)
                    or candidate_metadata.st_uid != os.getuid()
                    or stat.S_IMODE(candidate_metadata.st_mode) != 0o700
                    or (candidate_metadata.st_dev, candidate_metadata.st_ino)
                    != (path_metadata.st_dev, path_metadata.st_ino)
                ):
                    continue
                lease_fd = os.open(
                    ".lease",
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=candidate_fd,
                )
                lease_metadata = os.fstat(lease_fd)
                if (
                    not stat.S_ISREG(lease_metadata.st_mode)
                    or lease_metadata.st_uid != os.getuid()
                    or stat.S_IMODE(lease_metadata.st_mode) != 0o600
                    or lease_metadata.st_nlink != 1
                ):
                    continue
                fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                current_metadata = candidate.lstat()
                if (current_metadata.st_dev, current_metadata.st_ino) != (
                    candidate_metadata.st_dev,
                    candidate_metadata.st_ino,
                ):
                    continue
                shutil.rmtree(candidate)
            except OSError:
                continue
            finally:
                if lease_fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(lease_fd)
                if candidate_fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(candidate_fd)

    def prepare(self, specs: tuple[McpServerSpec, ...]) -> McpGeneration:
        if self._closed or not specs:
            raise McpHandoffError
        nonce = secrets.token_hex(16)
        root = self._root / f"{_GENERATION_PREFIX}{nonce}"
        try:
            root.mkdir(mode=0o700)
            agents = root / ".agents"
            agents.mkdir(mode=0o700)
            servers: dict[str, object] = {}
            overrides: list[tuple[str, str]] = []
            launcher_path = Path(sys.executable)
            if not launcher_path.is_absolute():
                raise McpHandoffError
            launcher = str(launcher_path)
            for index, spec in enumerate(specs):
                slot = f"{nonce.upper()}_{index}"
                servers[f"agy-acp-{nonce}-{index}"] = {
                    "command": launcher,
                    "args": ["-I", "-m", "agy_acp.mcp_launcher", slot],
                }
                overrides.append((MCP_ENV_PREFIX + slot, encode_launcher_spec(spec)))
            config = agents / "mcp_config.json"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(config, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    {"mcpServers": servers},
                    stream,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            self._active_roots.add(root)
            return McpGeneration(
                root=root,
                environment_overrides=tuple(overrides),
                _manager=self,
            )
        except (OSError, TypeError, ValueError):
            shutil.rmtree(root, ignore_errors=True)
            raise McpHandoffError from None

    def _close_generation(self, root: Path) -> None:
        if root.parent != self._root or root not in self._active_roots:
            raise McpHandoffError
        try:
            shutil.rmtree(root)
        except OSError:
            raise McpHandoffError from None
        self._active_roots.remove(root)

    def close(self) -> None:
        if self._closed:
            return
        cleanup_failed = False
        for root in list(self._active_roots):
            try:
                shutil.rmtree(root)
            except OSError:
                cleanup_failed = True
            else:
                self._active_roots.remove(root)
        if cleanup_failed:
            raise McpHandoffError
        try:
            if not self._root_removed:
                shutil.rmtree(self._root)
                self._root_removed = True
            if self._lease_fd is not None:
                os.close(self._lease_fd)
                self._lease_fd = None
        except OSError:
            raise McpHandoffError from None
        self._closed = True
