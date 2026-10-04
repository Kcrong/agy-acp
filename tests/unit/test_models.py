import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from agy_acp.errors import ModelDiscoveryError
from agy_acp.executable import AgyCommand
from agy_acp.models import AgyModel, discover_models, parse_model_listing

FIXTURE = Path(__file__).parents[1] / "fixtures" / "fake_agy.py"


def test_parse_model_listing_preserves_order_and_effort_families() -> None:
    models = parse_model_listing(
        b"gemini-flash-high\tGemini Flash (High)\n"
        b"gemini-flash-low\tGemini Flash (Low)\n"
        b"custom\tCustom Model\n"
    )

    assert models == (
        AgyModel(
            id="gemini-flash-high",
            name="Gemini Flash (High)",
            family_id="gemini-flash",
            effort="high",
        ),
        AgyModel(
            id="gemini-flash-low",
            name="Gemini Flash (Low)",
            family_id="gemini-flash",
            effort="low",
        ),
        AgyModel(id="custom", name="Custom Model", family_id="custom", effort=None),
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"missing-tab\n",
        b"model\t\n",
        b"bad model\tBad Model\n",
        b"duplicate\tFirst\nduplicate\tSecond\n",
        b"model\tControl\x00Name\n",
        b"\xff\n",
        b"model\tName\n\n",
        b"model\t" + b"x" * 257 + b"\n",
        b"x" * (256 * 1024 + 1),
    ],
)
def test_parse_model_listing_rejects_malformed_or_unbounded_output(raw: bytes) -> None:
    with pytest.raises(ModelDiscoveryError, match="Backend model discovery failed"):
        parse_model_listing(raw)


@pytest.mark.asyncio
async def test_discover_models_runs_bounded_agy_subcommand() -> None:
    command = AgyCommand(
        Path(sys.executable).resolve(),
        ("-u", str(FIXTURE), "normal"),
    )

    models = await discover_models(command, timeout=2)

    assert [model.id for model in models] == [
        "fake-model-high",
        "fake-model-medium",
        "fake-model-low",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["model-list-error", "model-list-malformed"])
async def test_discover_models_hides_backend_failure_output(mode: str) -> None:
    command = AgyCommand(
        Path(sys.executable).resolve(),
        ("-u", str(FIXTURE), mode),
    )

    with pytest.raises(ModelDiscoveryError, match="Backend model discovery failed") as raised:
        await discover_models(command, timeout=2)

    assert "credential-sentinel" not in str(raised.value)


@pytest.mark.asyncio
async def test_discover_models_terminates_timeout() -> None:
    command = AgyCommand(
        Path(sys.executable).resolve(),
        ("-u", str(FIXTURE), "model-list-slow"),
    )
    started = time.monotonic()

    with pytest.raises(ModelDiscoveryError, match="Backend model discovery failed"):
        await discover_models(command, timeout=0.05)

    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_discover_models_preserves_cancellation() -> None:
    command = AgyCommand(
        Path(sys.executable).resolve(),
        ("-u", str(FIXTURE), "model-list-slow"),
    )
    task = asyncio.create_task(discover_models(command, timeout=30))
    await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def _wait_for_path(path: Path) -> None:
    async with asyncio.timeout(2):
        while not path.exists():
            await asyncio.sleep(0.01)


async def _wait_for_process_exit(pid: int) -> None:
    async with asyncio.timeout(2):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_discovery_timeout_covers_process_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn_cancelled = asyncio.Event()

    async def stalled_spawn(
        *_args: object,
        **_kwargs: object,
    ) -> asyncio.subprocess.Process:
        try:
            await asyncio.Event().wait()
        finally:
            spawn_cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", stalled_spawn)
    command = AgyCommand(Path(sys.executable).resolve())

    async with asyncio.timeout(0.2):
        with pytest.raises(ModelDiscoveryError, match="Backend model discovery failed"):
            await discover_models(command, timeout=0.05)
    assert spawn_cancelled.is_set()


@pytest.mark.asyncio
async def test_discovery_timeout_kills_stubborn_descendant(tmp_path: Path) -> None:
    command = AgyCommand(
        Path(sys.executable).resolve(),
        ("-u", str(FIXTURE), "model-list-descendant", str(tmp_path)),
    )

    with pytest.raises(ModelDiscoveryError, match="Backend model discovery failed"):
        await discover_models(command, timeout=0.05)

    pid_path = tmp_path / "model-child-pid"
    await _wait_for_path(pid_path)
    await _wait_for_process_exit(int(pid_path.read_text(encoding="utf-8")))


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_process_group_cleanup(tmp_path: Path) -> None:
    command = AgyCommand(
        Path(sys.executable).resolve(),
        ("-u", str(FIXTURE), "model-list-ignore-term", str(tmp_path)),
    )
    task = asyncio.create_task(discover_models(command, timeout=30))
    pid_path = tmp_path / "model-root-pid"
    await _wait_for_path(pid_path)

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await _wait_for_process_exit(int(pid_path.read_text(encoding="utf-8")))


@pytest.mark.asyncio
async def test_cancellation_during_timeout_cleanup_cannot_interrupt_owner(tmp_path: Path) -> None:
    command = AgyCommand(
        Path(sys.executable).resolve(),
        ("-u", str(FIXTURE), "model-list-ignore-term", str(tmp_path)),
    )
    task = asyncio.create_task(discover_models(command, timeout=0.05))
    pid_path = tmp_path / "model-root-pid"
    await _wait_for_path(pid_path)
    await asyncio.sleep(0.08)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    exception_contexts: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: exception_contexts.append(dict(context)))

    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert exception_contexts == []
    finally:
        loop.set_exception_handler(previous_handler)

    await _wait_for_process_exit(int(pid_path.read_text(encoding="utf-8")))


@pytest.mark.asyncio
@pytest.mark.parametrize("denied_signal", [signal.SIGTERM, signal.SIGKILL])
async def test_discovery_cleanup_polls_through_transient_darwin_eperm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    denied_signal: int,
) -> None:
    command = AgyCommand(
        Path(sys.executable).resolve(),
        ("-u", str(FIXTURE), "model-list-ignore-term", str(tmp_path)),
    )
    pid_path = tmp_path / "model-root-pid"
    original_killpg = os.killpg
    denied = False

    def transient_killpg(process_group_id: int, requested_signal: int) -> None:
        nonlocal denied
        if requested_signal == denied_signal and not denied:
            denied = True
            raise PermissionError
        original_killpg(process_group_id, requested_signal)

    monkeypatch.setattr(os, "killpg", transient_killpg)

    with pytest.raises(ModelDiscoveryError, match="Backend model discovery failed"):
        await discover_models(command, timeout=0.05)

    assert denied
    await _wait_for_path(pid_path)
    await _wait_for_process_exit(int(pid_path.read_text(encoding="utf-8")))
