import asyncio
import contextlib
import math
import os
import re
import signal
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from agy_acp.errors import ModelDiscoveryError

if TYPE_CHECKING:
    from agy_acp.executable import AgyCommand

AgyEffort = Literal["low", "medium", "high", "max"]
AGY_EFFORTS: tuple[AgyEffort, ...] = ("low", "medium", "high", "max")
_MAX_MODELS = 128
_MAX_STDOUT_BYTES = 256 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_MODEL_DISCOVERY_TIMEOUT = 15.0
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


@dataclass(frozen=True, slots=True)
class AgyModel:
    id: str
    name: str
    family_id: str
    effort: AgyEffort | None


@dataclass(frozen=True, slots=True)
class _DiscoveryOutcome:
    models: tuple[AgyModel, ...] | None = None
    error: BaseException | None = None


def _model_identity(model_id: str) -> tuple[str, AgyEffort | None]:
    for effort in AGY_EFFORTS:
        suffix = f"-{effort}"
        if model_id.endswith(suffix):
            family_id = model_id[: -len(suffix)]
            if family_id:
                return family_id, effort
    return model_id, None


def parse_model_listing(raw: bytes) -> tuple[AgyModel, ...]:
    if len(raw) > _MAX_STDOUT_BYTES:
        raise ModelDiscoveryError
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise ModelDiscoveryError from None
    lines = text.splitlines()
    if not lines or len(lines) > _MAX_MODELS or any(not line for line in lines):
        raise ModelDiscoveryError

    models: list[AgyModel] = []
    identifiers: set[str] = set()
    for line in lines:
        parts = line.split("\t")
        if len(parts) != 2:
            raise ModelDiscoveryError
        model_id, name = parts
        if (
            not _MODEL_ID.fullmatch(model_id)
            or model_id in identifiers
            or not name
            or len(name.encode("utf-8")) > 256
            or any(unicodedata.category(character).startswith("C") for character in name)
        ):
            raise ModelDiscoveryError
        family_id, effort = _model_identity(model_id)
        models.append(AgyModel(id=model_id, name=name, family_id=family_id, effort=effort))
        identifiers.add(model_id)
    return tuple(models)


async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise ModelDiscoveryError
        chunks.append(chunk)


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        raise ModelDiscoveryError from None
    return True


async def _wait_for_root(process: asyncio.subprocess.Process, timeout: float) -> None:
    if process.returncode is not None:
        return
    try:
        async with asyncio.timeout(timeout):
            await asyncio.shield(process.wait())
    except TimeoutError:
        return


async def _wait_for_process_group(process_group_id: int, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while _process_group_exists(process_group_id):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.01, remaining))
    return True


async def _force_process_group_gone(
    process: asyncio.subprocess.Process,
    timeout: float,
) -> None:
    process_group_id = process.pid
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        except (OSError, ValueError):
            raise ModelDiscoveryError from None
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ModelDiscoveryError
        await _wait_for_root(process, min(0.01, remaining))
        if not _process_group_exists(process_group_id):
            return
        await asyncio.sleep(min(0.01, remaining))


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    process_group_id = process.pid
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process_group_id, signal.SIGTERM)
    await _wait_for_root(process, 0.2)
    if await _wait_for_process_group(process_group_id, 0.2):
        return
    await _force_process_group_gone(process, 1.0)
    await _wait_for_root(process, 1.0)
    if process.returncode is None:
        raise ModelDiscoveryError


async def _cancel_readers(readers: Sequence[asyncio.Task[bytes]]) -> None:
    for reader in readers:
        reader.cancel()
    await asyncio.gather(*readers, return_exceptions=True)


async def _execute_model_command(
    command: "AgyCommand",
    process_holder: list[asyncio.subprocess.Process],
) -> bytes:
    process = await asyncio.create_subprocess_exec(
        str(command.executable),
        *command.prefix_args,
        "models",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    process_holder.append(process)
    stdout = process.stdout
    stderr = process.stderr
    if stdout is None or stderr is None:
        raise ModelDiscoveryError
    readers = (
        asyncio.create_task(_read_bounded(stdout, _MAX_STDOUT_BYTES)),
        asyncio.create_task(_read_bounded(stderr, _MAX_STDERR_BYTES)),
    )
    try:
        stdout_data, _stderr_data = await asyncio.gather(*readers)
    finally:
        await _cancel_readers(readers)
    if await process.wait() != 0:
        raise ModelDiscoveryError
    return stdout_data


async def _discover_models_once(
    command: "AgyCommand",
    timeout: float,
    cancellation: asyncio.Event,
) -> tuple[AgyModel, ...]:
    process_holder: list[asyncio.subprocess.Process] = []
    worker = asyncio.create_task(
        _execute_model_command(command, process_holder),
        name="agy-acp.model-discovery-command",
    )
    cancellation_wait = asyncio.create_task(
        cancellation.wait(),
        name="agy-acp.model-discovery-cancellation",
    )
    try:
        done, _pending = await asyncio.wait(
            (worker, cancellation_wait),
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if worker in done:
            try:
                stdout_data = worker.result()
            except BaseException:
                if process_holder:
                    await _terminate_process(process_holder[0])
                raise ModelDiscoveryError from None
            process = process_holder[0]
            if _process_group_exists(process.pid):
                await _terminate_process(process)
                raise ModelDiscoveryError
            return parse_model_listing(stdout_data)

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        if process_holder:
            await _terminate_process(process_holder[0])
        raise ModelDiscoveryError
    finally:
        cancellation_wait.cancel()
        await asyncio.gather(cancellation_wait, return_exceptions=True)
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


async def _capture_discovery(
    command: "AgyCommand",
    timeout: float,
    cancellation: asyncio.Event,
) -> _DiscoveryOutcome:
    try:
        models = await _discover_models_once(command, timeout, cancellation)
    except BaseException as error:
        return _DiscoveryOutcome(error=error)
    return _DiscoveryOutcome(models=models)


async def discover_models(
    command: "AgyCommand",
    *,
    timeout: float = _MODEL_DISCOVERY_TIMEOUT,
) -> tuple[AgyModel, ...]:
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise ModelDiscoveryError
    try:
        timeout_seconds = float(timeout)
    except OverflowError:
        raise ModelDiscoveryError from None
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ModelDiscoveryError

    cancellation = asyncio.Event()
    supervisor = asyncio.create_task(
        _capture_discovery(command, timeout_seconds, cancellation),
        name="agy-acp.model-discovery",
    )
    cancellation_requested = False
    while True:
        try:
            outcome = await asyncio.shield(supervisor)
            break
        except asyncio.CancelledError:
            cancellation_requested = True
            cancellation.set()
            if supervisor.done():
                outcome = supervisor.result()
                break
    if cancellation_requested:
        raise asyncio.CancelledError
    if outcome.error is not None:
        if isinstance(outcome.error, ModelDiscoveryError):
            raise outcome.error
        raise ModelDiscoveryError from None
    if outcome.models is None:
        raise ModelDiscoveryError
    return outcome.models
