import asyncio
import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from agy_acp.agent import AgentConfig, AgyAgent
from agy_acp.config import AgyProcessConfig
from agy_acp.errors import McpHandoffError
from agy_acp.executable import AgyCommand
from agy_acp.mcp import McpWorkspaceManager
from agy_acp.process import AgyProcess
from agy_acp.protocol import AcpRequestError

FAKE_AGY = Path(__file__).parents[1] / "fixtures" / "fake_agy.py"
FAKE_MCP = Path(__file__).parents[1] / "fixtures" / "fake_mcp_server.py"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def agent_config(
    mode: str,
    marker_root: Path,
    mcp_temp_parent: Path,
    *,
    prompt_timeout: float = 3,
) -> AgentConfig:
    return AgentConfig(
        command=AgyCommand(
            Path(sys.executable).resolve(),
            ("-u", str(FAKE_AGY), mode, str(marker_root)),
        ),
        mcp_temp_parent=mcp_temp_parent,
        max_line_bytes=4096,
        max_stderr_bytes=64,
        max_pending_events=8,
        max_sessions=8,
        init_timeout=2,
        write_timeout=1,
        prompt_timeout=prompt_timeout,
        cancel_grace=0.1,
        kill_grace=1,
    )


def server(
    *,
    name: str,
    marker: Path,
    workspace: Path,
    environment_name: str,
    environment_value: str,
    forbidden_name: str,
    survivor: Path | None = None,
) -> dict[str, object]:
    args = [
        "-u",
        str(FAKE_MCP),
        str(marker),
        environment_name,
        digest(environment_value),
        forbidden_name,
        digest(str(workspace.resolve())),
    ]
    if survivor is not None:
        args.append(str(survivor))
    return {
        "name": name,
        "command": str(Path(sys.executable).resolve()),
        "args": args,
        "env": [{"name": environment_name, "value": environment_value}],
    }


async def wait_for_path(path: Path) -> None:
    async with asyncio.timeout(3):
        while not path.exists():
            await asyncio.sleep(0.01)


def read_marker(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def generation_roots(temp_parent: Path) -> list[Path]:
    return list(temp_parent.glob("agy-acp-mcp-owner-*/generation-*"))


@pytest.mark.asyncio
async def test_mcp_starts_lazily_and_active_config_lives_until_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    marker_root = tmp_path / "markers"
    temp_parent = tmp_path / "mcp-temp"
    user_directory = tmp_path / "user-directory"
    for path in (workspace, marker_root, temp_parent, user_directory):
        path.mkdir()
    marker = marker_root / "server.json"
    survivor = marker_root / "server-survived"
    sitecustomize_loaded = marker_root / "sitecustomize-loaded"
    package_loaded = marker_root / "workspace-package-loaded"
    (workspace / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sitecustomize_loaded)!r}).write_text('loaded', encoding='utf-8')\n",
        encoding="utf-8",
    )
    hostile_package = workspace / "agy_acp"
    hostile_package.mkdir()
    (hostile_package / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(package_loaded)!r}).write_text('loaded', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (hostile_package / "mcp_launcher.py").write_text("raise SystemExit(91)\n", encoding="utf-8")
    monkeypatch.setenv("AGY_ACP_MCP_SPEC_STALE", "stale-credential-sentinel")
    monkeypatch.setenv("PYTHONHOME", str(workspace / "hostile-home"))
    monkeypatch.setenv("PYTHONPATH", str(workspace))
    secret = "credential-sentinel"

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config("mcp-hang", marker_root, temp_parent),
        send_update,
    )
    session = await agent.new_session(
        cwd=str(workspace),
        mcp_servers=[
            server(
                name="project-name-collision",
                marker=marker,
                workspace=workspace,
                environment_name="SESSION_SECRET",
                environment_value=secret,
                forbidden_name="OTHER_SESSION_SECRET",
                survivor=survivor,
            )
        ],
        additional_directories=[str(user_directory)],
    )

    assert not marker.exists()
    assert not generation_roots(temp_parent)
    prompting = asyncio.create_task(
        agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "start"}],
        )
    )
    await wait_for_path(marker)

    assert read_marker(marker) == {
        "expected_env": True,
        "forbidden_env": True,
        "internal_specs_absent": True,
        "python_controls_absent": True,
        "cwd_preserved": True,
    }
    assert not sitecustomize_loaded.exists()
    assert not package_loaded.exists()
    assert read_marker(marker_root / "agy-environment.json") == {
        "stale_spec_absent": True,
        "python_controls_absent": True,
    }
    active_roots = generation_roots(temp_parent)
    assert len(active_roots) == 1
    config = active_roots[0] / ".agents" / "mcp_config.json"
    config_text = config.read_text(encoding="utf-8")
    config_payload = json.loads(config_text)
    launch = next(iter(config_payload["mcpServers"].values()))
    assert launch["args"][:3] == ["-I", "-m", "agy_acp.mcp_launcher"]
    assert secret not in config_text
    assert "project-name-collision" not in config_text
    assert stat.S_IMODE(active_roots[0].stat().st_mode) == 0o700
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert json.loads((marker_root / "mcp-add-dirs.json").read_text(encoding="utf-8")) == [
        str(user_directory),
        str(active_roots[0]),
    ]

    await agent.cancel(session.session_id)
    assert (await prompting).stop_reason == "cancelled"
    assert not generation_roots(temp_parent)
    await asyncio.sleep(1.3)
    assert not survivor.exists()
    await agent.close()
    assert not list(temp_parent.iterdir())


@pytest.mark.asyncio
async def test_multiple_mcp_servers_and_concurrent_sessions_are_isolated(
    tmp_path: Path,
) -> None:
    marker_root = tmp_path / "markers"
    temp_parent = tmp_path / "mcp-temp"
    first_workspace = tmp_path / "first-workspace"
    second_workspace = tmp_path / "second-workspace"
    for path in (marker_root, temp_parent, first_workspace, second_workspace):
        path.mkdir()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config("mcp-unique", marker_root, temp_parent),
        send_update,
    )
    first_markers = [marker_root / "first-a.json", marker_root / "first-b.json"]
    second_marker = marker_root / "second.json"
    first, second = await asyncio.gather(
        agent.new_session(
            cwd=str(first_workspace),
            mcp_servers=[
                server(
                    name="shared-project-name",
                    marker=first_markers[0],
                    workspace=first_workspace,
                    environment_name="FIRST_A_SECRET",
                    environment_value="first-a-sentinel",
                    forbidden_name="SECOND_SECRET",
                ),
                server(
                    name="second-server",
                    marker=first_markers[1],
                    workspace=first_workspace,
                    environment_name="FIRST_B_SECRET",
                    environment_value="first-b-sentinel",
                    forbidden_name="SECOND_SECRET",
                ),
            ],
        ),
        agent.new_session(
            cwd=str(second_workspace),
            mcp_servers=[
                server(
                    name="shared-project-name",
                    marker=second_marker,
                    workspace=second_workspace,
                    environment_name="SECOND_SECRET",
                    environment_value="second-sentinel",
                    forbidden_name="FIRST_A_SECRET",
                )
            ],
        ),
    )

    responses = await asyncio.gather(
        agent.prompt(
            session_id=first.session_id,
            prompt=[{"type": "text", "text": "first"}],
        ),
        agent.prompt(
            session_id=second.session_id,
            prompt=[{"type": "text", "text": "second"}],
        ),
    )

    assert [response.stop_reason for response in responses] == ["end_turn", "end_turn"]
    for marker in (*first_markers, second_marker):
        assert read_marker(marker) == {
            "expected_env": True,
            "forbidden_env": True,
            "internal_specs_absent": True,
            "python_controls_absent": True,
            "cwd_preserved": True,
        }
    assert not generation_roots(temp_parent)
    await agent.close()
    assert not list(temp_parent.iterdir())


@pytest.mark.asyncio
async def test_mcp_launcher_exec_failure_is_fixed_and_cleans_generation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    marker_root = tmp_path / "markers"
    temp_parent = tmp_path / "mcp-temp"
    for path in (workspace, marker_root, temp_parent):
        path.mkdir()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config("mcp", marker_root, temp_parent), send_update)
    secret = "credential-sentinel"
    target = tmp_path / "private-removed-command"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o700)
    session = await agent.new_session(
        cwd=str(workspace),
        mcp_servers=[
            {
                "name": "server",
                "command": str(target),
                "args": [],
                "env": [{"name": "TOKEN", "value": secret}],
            }
        ],
    )
    target.unlink()

    with pytest.raises(AcpRequestError, match="Backend unavailable") as raised:
        await agent.prompt(
            session_id=session.session_id,
            prompt=[{"type": "text", "text": "start"}],
        )
    assert secret not in str(raised.value)
    assert str(target) not in str(raised.value)
    assert not generation_roots(temp_parent)
    await agent.close()
    assert not list(temp_parent.iterdir())


@pytest.mark.asyncio
async def test_initial_backend_failure_cleans_mcp_generation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    marker_root = tmp_path / "markers"
    temp_parent = tmp_path / "mcp-temp"
    for path in (workspace, marker_root, temp_parent):
        path.mkdir()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config("no-init", marker_root, temp_parent), send_update)
    with pytest.raises(AcpRequestError, match="Backend unavailable"):
        await agent.new_session(
            cwd=str(workspace),
            mcp_servers=[
                server(
                    name="server",
                    marker=marker_root / "unused.json",
                    workspace=workspace,
                    environment_name="TOKEN",
                    environment_value="credential-sentinel",
                    forbidden_name="OTHER_TOKEN",
                )
            ],
        )
    assert not generation_roots(temp_parent)
    await agent.close()
    assert not list(temp_parent.iterdir())


@pytest.mark.asyncio
async def test_cancellation_during_spawn_cleans_mcp_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    marker_root = tmp_path / "markers"
    temp_parent = tmp_path / "mcp-temp"
    for path in (workspace, marker_root, temp_parent):
        path.mkdir()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    entered = asyncio.Event()
    release = asyncio.Event()
    original_spawn = AgyProcess._spawn
    original_close = McpWorkspaceManager._close_generation
    close_calls: list[Path] = []

    async def delayed_spawn(
        config: AgyProcessConfig,
        argv: tuple[str, ...],
    ) -> asyncio.subprocess.Process:
        entered.set()
        await release.wait()
        return await original_spawn(config, argv)

    def track_close(manager: McpWorkspaceManager, root: Path) -> None:
        close_calls.append(root)
        original_close(manager, root)

    agent = AgyAgent(agent_config("normal", marker_root, temp_parent), send_update)
    with monkeypatch.context() as patch:
        patch.setattr(AgyProcess, "_spawn", staticmethod(delayed_spawn))
        patch.setattr(McpWorkspaceManager, "_close_generation", track_close)
        creating = asyncio.create_task(
            agent.new_session(
                cwd=str(workspace),
                mcp_servers=[
                    server(
                        name="server",
                        marker=marker_root / "unused.json",
                        workspace=workspace,
                        environment_name="TOKEN",
                        environment_value="credential-sentinel",
                        forbidden_name="OTHER_TOKEN",
                    )
                ],
            )
        )
        await entered.wait()
        creating.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await creating

    assert len(close_calls) == 1
    assert not generation_roots(temp_parent)
    await agent.close()
    assert not list(temp_parent.iterdir())


@pytest.mark.asyncio
async def test_generation_cleanup_failure_remains_owned_for_agent_close_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    marker_root = tmp_path / "markers"
    temp_parent = tmp_path / "mcp-temp"
    for path in (workspace, marker_root, temp_parent):
        path.mkdir()

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(agent_config("mcp", marker_root, temp_parent), send_update)
    session = await agent.new_session(
        cwd=str(workspace),
        mcp_servers=[
            server(
                name="server",
                marker=marker_root / "server.json",
                workspace=workspace,
                environment_name="TOKEN",
                environment_value="credential-sentinel",
                forbidden_name="OTHER_TOKEN",
            )
        ],
    )
    original_close = McpWorkspaceManager._close_generation
    failed = False

    def fail_once(manager: McpWorkspaceManager, root: Path) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise McpHandoffError
        original_close(manager, root)

    with monkeypatch.context() as patch:
        patch.setattr(McpWorkspaceManager, "_close_generation", fail_once)
        with pytest.raises(AcpRequestError, match="Backend unavailable"):
            await agent.prompt(
                session_id=session.session_id,
                prompt=[{"type": "text", "text": "start"}],
            )
        assert len(generation_roots(temp_parent)) == 1
        await agent.close()

    assert failed
    assert not list(temp_parent.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["timeout", "session-close"])
async def test_active_mcp_generation_cleans_on_timeout_or_session_close(
    tmp_path: Path,
    termination: str,
) -> None:
    workspace = tmp_path / "workspace"
    marker_root = tmp_path / "markers"
    temp_parent = tmp_path / "mcp-temp"
    for path in (workspace, marker_root, temp_parent):
        path.mkdir()
    marker = marker_root / "server.json"

    async def send_update(_session_id: str, _update: dict[str, object]) -> None:
        return None

    agent = AgyAgent(
        agent_config(
            "mcp-hang",
            marker_root,
            temp_parent,
            prompt_timeout=0.5 if termination == "timeout" else 3,
        ),
        send_update,
    )
    session = await agent.new_session(
        cwd=str(workspace),
        mcp_servers=[
            server(
                name="server",
                marker=marker,
                workspace=workspace,
                environment_name="TOKEN",
                environment_value="credential-sentinel",
                forbidden_name="OTHER_TOKEN",
            )
        ],
    )

    if termination == "timeout":
        with pytest.raises(AcpRequestError, match="Prompt timed out"):
            await agent.prompt(
                session_id=session.session_id,
                prompt=[{"type": "text", "text": "start"}],
            )
    else:
        prompting = asyncio.create_task(
            agent.prompt(
                session_id=session.session_id,
                prompt=[{"type": "text", "text": "start"}],
            )
        )
        await wait_for_path(marker)
        await agent.close_session(session.session_id)
        assert (await prompting).stop_reason == "cancelled"

    assert marker.exists()
    assert not generation_roots(temp_parent)
    await agent.close()
    assert not list(temp_parent.iterdir())
