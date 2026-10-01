from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
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
_OWNER_NAME = re.compile(rf"{re.escape(_OWNER_PREFIX)}[0-9a-f]{{32}}")
_GENERATION_PREFIX = "generation-"
_MAX_SCAVENGE_ENTRIES = 128
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


def _is_owner_name(name: str) -> bool:
    return _OWNER_NAME.fullmatch(name) is not None


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _parent_is_safe(metadata: os.stat_result, root_owner_uid: int) -> bool:
    mode = stat.S_IMODE(metadata.st_mode)
    private_owner = metadata.st_uid == os.getuid() and mode & 0o022 == 0
    sticky_owner = bool(metadata.st_mode & stat.S_ISVTX) and metadata.st_uid in {
        os.getuid(),
        root_owner_uid,
    }
    return stat.S_ISDIR(metadata.st_mode) and (private_owner or sticky_owner)


def _safe_temp_parent(parent: Path) -> tuple[Path, tuple[int, int], int]:
    try:
        resolved = parent.resolve(strict=True)
        metadata = resolved.lstat()
        root_owner_uid = Path(resolved.anchor).lstat().st_uid
    except (OSError, RuntimeError):
        raise McpHandoffError from None
    if not _parent_is_safe(metadata, root_owner_uid):
        raise McpHandoffError
    return resolved, _identity(metadata), root_owner_uid


def _create_owner_root(parent: Path, parent_fd: int) -> tuple[Path, tuple[int, int]]:
    for _attempt in range(16):
        root = parent / f"{_OWNER_PREFIX}{secrets.token_hex(16)}"
        try:
            os.mkdir(root.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        try:
            root_identity = _identity(os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False))
        except OSError:
            with contextlib.suppress(OSError):
                os.rmdir(root.name, dir_fd=parent_fd)
            raise
        return root, root_identity
    raise McpHandoffError


class McpWorkspaceManager:
    def __init__(self, *, temp_parent: Path | None = None) -> None:
        requested_parent = Path(tempfile.gettempdir()) if temp_parent is None else temp_parent
        self._parent, parent_identity, root_owner_uid = _safe_temp_parent(requested_parent)
        self._parent_fd: int | None = None
        self._root_fd: int | None = None
        self._lease_fd: int | None = None
        created_root_identity: tuple[int, int] | None = None
        try:
            self._parent_fd = os.open(self._parent, _directory_flags())
            parent_metadata = os.fstat(self._parent_fd)
            parent_path_metadata = self._parent.lstat()
            if (
                _identity(parent_metadata) != parent_identity
                or _identity(parent_path_metadata) != parent_identity
                or not _parent_is_safe(parent_metadata, root_owner_uid)
            ):
                raise ValueError
            self._scavenge()
            self._root, created_root_identity = _create_owner_root(
                self._parent,
                self._parent_fd,
            )
            os.chmod(self._root.name, 0o700, dir_fd=self._parent_fd)
            self._root_fd = os.open(
                self._root.name,
                _directory_flags(),
                dir_fd=self._parent_fd,
            )
            root_metadata = os.fstat(self._root_fd)
            root_path_metadata = self._root.lstat()
            self._root_identity = _identity(root_metadata)
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            self._lease_fd = os.open(".lease", flags, 0o600, dir_fd=self._root_fd)
            os.fchmod(self._lease_fd, 0o600)
            lease_metadata = os.fstat(self._lease_fd)
            if (
                self._root_identity != _identity(root_path_metadata)
                or not stat.S_ISDIR(root_metadata.st_mode)
                or root_metadata.st_uid != os.getuid()
                or stat.S_IMODE(root_metadata.st_mode) != 0o700
                or not stat.S_ISREG(lease_metadata.st_mode)
                or lease_metadata.st_uid != os.getuid()
                or stat.S_IMODE(lease_metadata.st_mode) != 0o600
                or lease_metadata.st_nlink != 1
            ):
                raise ValueError
            fcntl.flock(self._lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (McpHandoffError, OSError, ValueError):
            root = getattr(self, "_root", None)
            if (
                isinstance(root, Path)
                and self._parent_fd is not None
                and created_root_identity is not None
            ):
                with contextlib.suppress(OSError):
                    current_identity = _identity(
                        os.stat(root.name, dir_fd=self._parent_fd, follow_symlinks=False)
                    )
                    if current_identity == created_root_identity:
                        if self._root_fd is not None:
                            with contextlib.suppress(OSError):
                                os.unlink(".lease", dir_fd=self._root_fd)
                        os.rmdir(root.name, dir_fd=self._parent_fd)
            for descriptor_name in ("_lease_fd", "_root_fd", "_parent_fd"):
                descriptor = getattr(self, descriptor_name)
                if descriptor is not None:
                    with contextlib.suppress(OSError):
                        os.close(descriptor)
                    setattr(self, descriptor_name, None)
            raise McpHandoffError from None
        self._active_roots: dict[Path, tuple[int, int]] = {}
        self._root_removed = False
        self._closed = False

    @property
    def root(self) -> Path:
        return self._root

    def _validate_root_path(self) -> None:
        if self._root_fd is None:
            raise McpHandoffError
        try:
            descriptor_metadata = os.fstat(self._root_fd)
            path_metadata = self._root.lstat()
        except OSError:
            raise McpHandoffError from None
        if (
            _identity(descriptor_metadata) != self._root_identity
            or _identity(path_metadata) != self._root_identity
            or not stat.S_ISDIR(path_metadata.st_mode)
            or path_metadata.st_uid != os.getuid()
            or stat.S_IMODE(path_metadata.st_mode) != 0o700
        ):
            raise McpHandoffError

    def _scavenge(self) -> None:
        if self._parent_fd is None:
            raise McpHandoffError
        try:
            entries = os.scandir(self._parent_fd)
        except OSError:
            raise McpHandoffError from None
        visited = 0
        with entries:
            for entry in entries:
                visited += 1
                if visited > _MAX_SCAVENGE_ENTRIES:
                    break
                if not _is_owner_name(entry.name):
                    continue
                self._scavenge_candidate(entry.name)

    def _scavenge_candidate(self, candidate_name: str) -> None:
        if self._parent_fd is None:
            raise McpHandoffError
        candidate = self._parent / candidate_name
        candidate_fd: int | None = None
        lease_fd: int | None = None
        try:
            candidate_fd = os.open(
                candidate_name,
                _directory_flags(),
                dir_fd=self._parent_fd,
            )
            candidate_metadata = os.fstat(candidate_fd)
            path_metadata = candidate.lstat()
            if (
                not stat.S_ISDIR(candidate_metadata.st_mode)
                or candidate_metadata.st_uid != os.getuid()
                or stat.S_IMODE(candidate_metadata.st_mode) != 0o700
                or (candidate_metadata.st_dev, candidate_metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                return
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
                return
            fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current_metadata = candidate.lstat()
            if (current_metadata.st_dev, current_metadata.st_ino) != (
                candidate_metadata.st_dev,
                candidate_metadata.st_ino,
            ):
                return
            shutil.rmtree(candidate)
        except OSError:
            return
        finally:
            if lease_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(lease_fd)
            if candidate_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(candidate_fd)

    def prepare(self, specs: tuple[McpServerSpec, ...]) -> McpGeneration:
        if self._closed or not specs or self._root_fd is None:
            raise McpHandoffError
        self._validate_root_path()
        nonce = secrets.token_hex(16)
        generation_name = f"{_GENERATION_PREFIX}{nonce}"
        root = self._root / generation_name
        generation_fd: int | None = None
        agents_fd: int | None = None
        config_fd: int | None = None
        generation_identity: tuple[int, int] | None = None
        try:
            os.mkdir(generation_name, mode=0o700, dir_fd=self._root_fd)
            os.chmod(generation_name, 0o700, dir_fd=self._root_fd)
            generation_fd = os.open(
                generation_name,
                _directory_flags(),
                dir_fd=self._root_fd,
            )
            os.fchmod(generation_fd, 0o700)
            generation_metadata = os.fstat(generation_fd)
            generation_identity = _identity(generation_metadata)
            os.mkdir(".agents", mode=0o700, dir_fd=generation_fd)
            os.chmod(".agents", 0o700, dir_fd=generation_fd)
            agents_fd = os.open(".agents", _directory_flags(), dir_fd=generation_fd)
            os.fchmod(agents_fd, 0o700)
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
                    "args": ["-E", "-P", "-m", "agy_acp.mcp_launcher", slot],
                }
                overrides.append((MCP_ENV_PREFIX + slot, encode_launcher_spec(spec)))
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            config_fd = os.open(
                "mcp_config.json",
                flags,
                0o600,
                dir_fd=agents_fd,
            )
            os.fchmod(config_fd, 0o600)
            stream = os.fdopen(config_fd, "w", encoding="utf-8")
            config_fd = None
            with stream:
                json.dump(
                    {"mcpServers": servers},
                    stream,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            self._validate_root_path()
            path_metadata = root.lstat()
            if (
                _identity(path_metadata) != generation_identity
                or not stat.S_ISDIR(path_metadata.st_mode)
                or path_metadata.st_uid != os.getuid()
                or stat.S_IMODE(path_metadata.st_mode) != 0o700
            ):
                raise McpHandoffError
            self._active_roots[root] = generation_identity
            return McpGeneration(
                root=root,
                environment_overrides=tuple(overrides),
                _manager=self,
            )
        except (McpHandoffError, OSError, TypeError, ValueError):
            if generation_identity is not None:
                with contextlib.suppress(McpHandoffError, OSError):
                    self._remove_generation(root, generation_identity)
            raise McpHandoffError from None
        finally:
            for owned_fd in (config_fd, agents_fd, generation_fd):
                if owned_fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(owned_fd)

    def _remove_generation(self, root: Path, expected_identity: tuple[int, int]) -> None:
        self._validate_root_path()
        try:
            path_metadata = root.lstat()
        except OSError:
            raise McpHandoffError from None
        if _identity(path_metadata) != expected_identity:
            raise McpHandoffError
        try:
            shutil.rmtree(root)
        except OSError:
            raise McpHandoffError from None

    def _close_generation(self, root: Path) -> None:
        expected_identity = self._active_roots.get(root)
        if root.parent != self._root or expected_identity is None:
            raise McpHandoffError
        self._remove_generation(root, expected_identity)
        del self._active_roots[root]

    def close(self) -> None:
        if self._closed:
            return
        cleanup_failed = False
        for root, expected_identity in list(self._active_roots.items()):
            try:
                self._remove_generation(root, expected_identity)
            except McpHandoffError:
                cleanup_failed = True
            else:
                del self._active_roots[root]
        if cleanup_failed:
            raise McpHandoffError
        try:
            if not self._root_removed:
                self._validate_root_path()
                shutil.rmtree(self._root)
                self._root_removed = True
            for descriptor_name in ("_lease_fd", "_root_fd", "_parent_fd"):
                descriptor = getattr(self, descriptor_name)
                if descriptor is not None:
                    os.close(descriptor)
                    setattr(self, descriptor_name, None)
        except OSError:
            raise McpHandoffError from None
        self._closed = True
