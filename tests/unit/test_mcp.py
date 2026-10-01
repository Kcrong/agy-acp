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
    stale = tmp_path / "agy-acp-mcp-owner-stale"
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


def test_manager_skips_nonregular_stale_lease_without_blocking(tmp_path: Path) -> None:
    stale = tmp_path / "agy-acp-mcp-owner-fifo"
    stale.mkdir(mode=0o700)
    lease = stale / ".lease"
    os.mkfifo(lease, mode=0o600)

    manager = McpWorkspaceManager(temp_parent=tmp_path)

    assert stale.exists()
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
