from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agy_acp.errors import InvalidMcpConfigError, McpHandoffError
from agy_acp.mcp import (
    MCP_ENV_PREFIX,
    McpServerSpec,
    McpWorkspaceManager,
    parse_mcp_servers,
    resolve_mcp_servers,
)


def raw_server(
    *,
    name: str = "server",
    command: str = "/usr/bin/example",
    args: list[object] | None = None,
    env: list[object] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "command": command,
        "args": ["--safe"] if args is None else args,
        "env": [{"name": "TOKEN", "value": "credential-sentinel"}] if env is None else env,
    }


def test_parse_stdio_mcp_servers_is_strict_and_immutable() -> None:
    parsed = parse_mcp_servers(
        [
            {
                **raw_server(),
                "_meta": {"ignored": True},
                "env": [
                    {"name": "TOKEN", "value": "credential-sentinel", "_meta": {}},
                    {"name": "EMPTY", "value": ""},
                ],
            }
        ]
    )

    assert parsed == (
        McpServerSpec(
            name="server",
            command="/usr/bin/example",
            args=("--safe",),
            env=(("TOKEN", "credential-sentinel"), ("EMPTY", "")),
        ),
    )


@pytest.mark.parametrize(
    "servers",
    [
        [None],
        [{"type": "http", "name": "x", "url": "https://example.invalid", "headers": []}],
        [{"type": "sse", "name": "x", "url": "https://example.invalid", "headers": []}],
        [{"type": "acp", "name": "x", "serverId": "id"}],
        [raw_server(name="")],
        [raw_server(command="")],
        [raw_server(args=[1])],
        [raw_server(args=["safe\x00tail"])],
        [raw_server(env=[{"name": "A", "value": "1"}, {"name": "A", "value": "2"}])],
        [raw_server(env=[{"name": "A=B", "value": "1"}])],
        [raw_server(env=[{"name": "A", "value": "safe\x00tail"}])],
        [raw_server(), raw_server()],
        [{**raw_server(), "unexpected": "credential-sentinel"}],
    ],
)
def test_parse_mcp_servers_rejects_invalid_input_without_echo(
    servers: list[object],
) -> None:
    with pytest.raises(InvalidMcpConfigError, match="Invalid MCP server configuration") as raised:
        parse_mcp_servers(servers)
    assert "credential-sentinel" not in str(raised.value)


def test_resolve_mcp_servers_canonicalizes_absolute_and_safe_bare_commands() -> None:
    executable = Path(sys.executable).resolve()
    absolute, bare = resolve_mcp_servers(
        parse_mcp_servers(
            [
                raw_server(name="absolute", command=str(executable), env=[]),
                raw_server(
                    name="bare",
                    command=executable.name,
                    env=[{"name": "PATH", "value": str(executable.parent)}],
                ),
            ]
        )
    )

    assert absolute.command == str(executable)
    assert bare.command == str(executable)


@pytest.mark.parametrize("command", ["./project-server", "missing-server"])
def test_resolve_mcp_servers_rejects_unsafe_command_without_echo(command: str) -> None:
    with pytest.raises(InvalidMcpConfigError, match="Invalid MCP server configuration") as raised:
        resolve_mcp_servers(parse_mcp_servers([raw_server(command=command, env=[])]))
    assert command not in str(raised.value)


def test_safe_launcher_flags_retain_user_site_lookup(tmp_path: Path) -> None:
    base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    environment["PYTHONHOME"] = str(tmp_path / "hostile-home")
    environment["PYTHONPATH"] = str(workspace)
    query = subprocess.run(
        [
            str(base_executable),
            "-E",
            "-P",
            "-c",
            "import site; print(site.ENABLE_USER_SITE); print(site.getusersitepackages())",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    lines = query.stdout.splitlines()
    assert query.returncode == 0
    assert lines[0] == "True"
    user_site = Path(lines[1])
    package = user_site / "agy_acp"
    source_package = Path(__file__).parents[2] / "src" / "agy_acp"
    package.parent.mkdir(parents=True)
    shutil.copytree(source_package, package)
    hostile = workspace / "agy_acp"
    hostile.mkdir()
    (hostile / "__init__.py").write_text("", encoding="utf-8")
    (hostile / "mcp_launcher.py").write_text("raise SystemExit(91)\n", encoding="utf-8")

    completed = subprocess.run(
        [str(base_executable), "-E", "-P", "-m", "agy_acp.mcp_launcher", "INVALID_SLOT"],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 127
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_generation_config_contains_no_client_spec_values(tmp_path: Path) -> None:
    manager = McpWorkspaceManager(temp_parent=tmp_path)
    specs = parse_mcp_servers(
        [
            raw_server(
                command="private-command-sentinel",
                args=["private-argument-sentinel"],
            )
        ]
    )
    generation = manager.prepare(specs)
    config = generation.root / ".agents" / "mcp_config.json"
    config_text = config.read_text(encoding="utf-8")
    environment = dict(generation.environment_overrides)

    assert stat.S_IMODE(generation.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert "private-command-sentinel" not in config_text
    assert "private-argument-sentinel" not in config_text
    assert "credential-sentinel" not in config_text
    assert "agy_acp.mcp_launcher" in config_text
    assert len(environment) == 1
    assert all(name.startswith(MCP_ENV_PREFIX) for name in environment)
    assert "private-command-sentinel" in next(iter(environment.values()))

    generation.close()
    assert not generation.root.exists()
    manager.close()


def test_generations_use_distinct_config_keys_and_roots(tmp_path: Path) -> None:
    manager = McpWorkspaceManager(temp_parent=tmp_path)
    specs = parse_mcp_servers([raw_server()])
    first = manager.prepare(specs)
    second = manager.prepare(specs)

    first_config = json.loads(
        (first.root / ".agents" / "mcp_config.json").read_text(encoding="utf-8")
    )
    second_config = json.loads(
        (second.root / ".agents" / "mcp_config.json").read_text(encoding="utf-8")
    )
    assert first.root != second.root
    assert set(first_config["mcpServers"]) != set(second_config["mcpServers"])
    assert set(dict(first.environment_overrides)).isdisjoint(dict(second.environment_overrides))

    first.close()
    second.close()
    manager.close()


def test_launcher_execs_target_with_only_own_server_environment(tmp_path: Path) -> None:
    output = tmp_path / "target.json"
    target = tmp_path / "target.py"
    target.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_text(json.dumps({"
        "'own': os.environ.get('OWN_SECRET'), "
        "'spec_keys': sorted(k for k in os.environ if k.startswith('AGY_ACP_MCP_SPEC_')), "
        "'python_controls': sorted(k for k in ('PYTHONHOME', 'PYTHONPATH') if k in os.environ)"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    manager = McpWorkspaceManager(temp_parent=tmp_path)
    generation = manager.prepare(
        (
            McpServerSpec(
                name="server",
                command=str(Path(sys.executable).resolve()),
                args=(str(target), str(output)),
                env=(("OWN_SECRET", "credential-sentinel"),),
            ),
        )
    )
    config = json.loads(
        (generation.root / ".agents" / "mcp_config.json").read_text(encoding="utf-8")
    )
    launch = next(iter(config["mcpServers"].values()))
    environment = dict(os.environ)
    environment.update(dict(generation.environment_overrides))
    environment[MCP_ENV_PREFIX + "UNRELATED"] = "credential-other-sentinel"
    environment["PYTHONHOME"] = str(tmp_path / "hostile-home")
    environment["PYTHONPATH"] = str(tmp_path)

    completed = subprocess.run(
        [launch["command"], *launch["args"]],
        env=environment,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "own": "credential-sentinel",
        "spec_keys": [],
        "python_controls": [],
    }
    generation.close()
    manager.close()


def test_manager_scavenges_unlocked_stale_owner_root(tmp_path: Path) -> None:
    stale = tmp_path / ("agy-acp-mcp-owner-" + "a" * 32)
    stale.mkdir(mode=0o700)
    lease = stale / ".lease"
    lease.write_text("", encoding="utf-8")
    lease.chmod(0o600)
    (stale / "residue").write_text("non-sensitive", encoding="utf-8")

    manager = McpWorkspaceManager(temp_parent=tmp_path)

    assert not stale.exists()
    manager.close()


def test_manager_canonicalizes_safe_parent_and_rejects_unsafe_shared_parent(
    tmp_path: Path,
) -> None:
    safe = tmp_path / "safe"
    safe.mkdir(mode=0o700)
    alias = tmp_path / "safe-alias"
    alias.symlink_to(safe, target_is_directory=True)
    manager = McpWorkspaceManager(temp_parent=alias)
    assert manager.root.parent == safe.resolve()
    manager.close()

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(McpHandoffError, match="MCP handoff failed"):
        McpWorkspaceManager(temp_parent=unsafe)

    unsafe_ancestor = tmp_path / "unsafe-ancestor"
    unsafe_ancestor.mkdir()
    unsafe_ancestor.chmod(0o777)
    nested_private = unsafe_ancestor / "private"
    nested_private.mkdir(mode=0o700)
    manager = McpWorkspaceManager(temp_parent=nested_private)
    original_parent = unsafe_ancestor / "original-private"
    nested_private.rename(original_parent)
    nested_private.mkdir(mode=0o700)
    with pytest.raises(McpHandoffError, match="MCP handoff failed"):
        manager.prepare(parse_mcp_servers([raw_server()]))
    assert not list(nested_private.iterdir())
    nested_private.rmdir()
    original_parent.rename(nested_private)
    manager.close()


def test_manager_rejects_parent_substitution_before_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    original_parent = tmp_path / "original-parent"
    original_open = os.open
    substituted = False

    def substitute_before_open(path: Any, *args: Any, **kwargs: Any) -> int:
        nonlocal substituted
        if Path(path) == parent and kwargs.get("dir_fd") is None and not substituted:
            parent.rename(original_parent)
            parent.mkdir(mode=0o700)
            substituted = True
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("agy_acp.mcp.os.open", substitute_before_open)
        with pytest.raises(McpHandoffError, match="MCP handoff failed"):
            McpWorkspaceManager(temp_parent=parent)

    assert substituted
    assert not list(parent.iterdir())
    parent.rmdir()
    original_parent.rename(parent)


def test_manager_rejects_sticky_parent_not_owned_by_current_or_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "sticky"
    parent.mkdir()
    parent.chmod(0o1777)
    different_uid = os.getuid() + 1

    with monkeypatch.context() as patch:
        patch.setattr("agy_acp.mcp.os.getuid", lambda: different_uid)
        with pytest.raises(McpHandoffError, match="MCP handoff failed"):
            McpWorkspaceManager(temp_parent=parent)


def test_manager_caps_total_nonmatching_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(129):
        (tmp_path / f"unrelated-{index:032x}").touch()
    examined: list[str] = []

    def reject_name(name: str) -> bool:
        examined.append(name)
        return False

    with monkeypatch.context() as patch:
        patch.setattr("agy_acp.mcp._is_owner_name", reject_name)
        manager = McpWorkspaceManager(temp_parent=tmp_path)

    assert len(examined) == 128
    manager.close()


def test_manager_caps_stale_candidate_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(129):
        (tmp_path / f"agy-acp-mcp-owner-{index:032x}").mkdir()
    examined: list[str] = []

    def record_candidate(_manager: McpWorkspaceManager, candidate_name: str) -> None:
        examined.append(candidate_name)

    with monkeypatch.context() as patch:
        patch.setattr(McpWorkspaceManager, "_scavenge_candidate", record_candidate)
        manager = McpWorkspaceManager(temp_parent=tmp_path)

    assert len(examined) == 128
    manager.close()


def test_manager_skips_nonregular_stale_lease_without_blocking(tmp_path: Path) -> None:
    stale = tmp_path / ("agy-acp-mcp-owner-" + "b" * 32)
    stale.mkdir(mode=0o700)
    lease = stale / ".lease"
    os.mkfifo(lease, mode=0o600)

    manager = McpWorkspaceManager(temp_parent=tmp_path)

    assert stale.exists()
    manager.close()


def test_manager_cleans_owner_root_when_inode_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_stat = os.stat

    def fail_owner_stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if (
            isinstance(path, str)
            and path.startswith("agy-acp-mcp-owner-")
            and kwargs.get("dir_fd") is not None
        ):
            raise OSError
        return original_stat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("agy_acp.mcp.os.stat", fail_owner_stat)
        with pytest.raises(McpHandoffError, match="MCP handoff failed"):
            McpWorkspaceManager(temp_parent=tmp_path)

    assert not list(tmp_path.glob("agy-acp-mcp-owner-*"))


def test_manager_cleans_owner_root_when_root_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = os.open

    def fail_owner_open(path: Any, *args: Any, **kwargs: Any) -> int:
        if (
            isinstance(path, str)
            and path.startswith("agy-acp-mcp-owner-")
            and kwargs.get("dir_fd") is not None
        ):
            raise OSError
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("agy_acp.mcp.os.open", fail_owner_open)
        with pytest.raises(McpHandoffError, match="MCP handoff failed"):
            McpWorkspaceManager(temp_parent=tmp_path)

    assert not list(tmp_path.glob("agy-acp-mcp-owner-*"))


def test_prepare_closes_config_descriptor_when_fdopen_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = McpWorkspaceManager(temp_parent=tmp_path)
    original_open = os.open
    original_close = os.close
    config_fds: list[int] = []
    closed_fds: list[int] = []

    def track_config_open(path: Any, *args: Any, **kwargs: Any) -> int:
        descriptor = original_open(path, *args, **kwargs)
        if path == "mcp_config.json":
            config_fds.append(descriptor)
        return descriptor

    def fail_fdopen(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError

    def track_close(descriptor: int) -> None:
        closed_fds.append(descriptor)
        original_close(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr("agy_acp.mcp.os.open", track_config_open)
        patch.setattr("agy_acp.mcp.os.fdopen", fail_fdopen)
        patch.setattr("agy_acp.mcp.os.close", track_close)
        with pytest.raises(McpHandoffError, match="MCP handoff failed"):
            manager.prepare(parse_mcp_servers([raw_server()]))

    assert len(config_fds) == 1
    assert config_fds[0] in closed_fds
    assert not list(manager.root.glob("generation-*"))
    manager.close()


def test_manager_close_retries_owner_cleanup_without_releasing_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = McpWorkspaceManager(temp_parent=tmp_path)
    owner_root = manager.root
    original_rmtree = shutil.rmtree
    failed = False

    def fail_once(path: str | Path, *args: Any, **kwargs: Any) -> None:
        nonlocal failed
        if Path(path) == owner_root and not failed:
            failed = True
            raise OSError
        original_rmtree(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("agy_acp.mcp.shutil.rmtree", fail_once)
        with pytest.raises(McpHandoffError, match="MCP handoff failed"):
            manager.close()
        assert owner_root.exists()
        manager.close()

    assert not owner_root.exists()


@pytest.mark.parametrize(
    "raw_spec",
    [None, "{credential-sentinel", json.dumps(raw_server(command="./project-server", env=[]))],
)
def test_launcher_rejects_missing_or_invalid_spec_without_output(
    raw_spec: str | None,
) -> None:
    environment = dict(os.environ)
    slot = "INVALID_SLOT"
    if raw_spec is not None:
        environment[MCP_ENV_PREFIX + slot] = raw_spec

    completed = subprocess.run(
        [sys.executable, "-m", "agy_acp.mcp_launcher", slot],
        env=environment,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 127
    assert completed.stdout == b""
    assert completed.stderr == b""
