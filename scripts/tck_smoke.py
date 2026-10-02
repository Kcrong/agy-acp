from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

_TCK_VERSION = "0.2.0"
_SCHEMA_REVISION = "6d08f412a7a1370d3cc9a124e3be3d6acf92641e"
_TIMEOUT_SECONDS = 300
_EXPECTED_REQUIREMENTS = 56
_EXPECTED_MANDATORY = 21
_TIERS = frozenset({"MANDATORY", "CAPABILITY", "ADVISORY", "INFORMATIONAL"})
_STATUSES = frozenset({"PASS", "FAIL", "SKIPPED", "NOT_TESTED"})
_REQUIRED_CAPABILITIES = frozenset(
    {
        "ACP-ADDDIRS-001",
        "ACP-CLOSE-001",
        "ACP-CLOSE-002",
        "ACP-RESUME-001",
        "ACP-RESUME-002",
    }
)


def _scratch_root() -> Path:
    for name in ("KIROCREW_SCRATCH", "RUNNER_TEMP"):
        value = os.environ.get(name)
        if value:
            root = Path(value)
            if root.is_dir():
                return root
            raise RuntimeError(f"{name} does not name an existing directory")
    return Path(tempfile.gettempdir())


def _safe_environment(root: Path) -> dict[str, str]:
    home = root / "home"
    temporary = root / "tmp"
    home.mkdir()
    temporary.mkdir()
    environment = {
        "HOME": str(home),
        "NO_COLOR": "1",
        "PATH": os.defpath,
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "TMPDIR": str(temporary),
        "USERPROFILE": str(home),
    }
    for name in ("LANG", "LC_ALL"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _create_agy_wrapper(root: Path) -> Path:
    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "fake_agy.py"
    if not fixture.is_file():
        raise RuntimeError("TCK backend fixture is missing")
    wrapper = root / "agy-tck-backend"
    wrapper.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " -u "
        + shlex.quote(str(fixture))
        + ' tck "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    return wrapper


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


def _execute_tck(root: Path, report: Path) -> tuple[int, int, int]:
    workspace = root / "workspace"
    workspace.mkdir()
    wrapper = _create_agy_wrapper(root)
    command = [
        sys.executable,
        "-m",
        "tck",
        "--protocol-version",
        "1",
        "--agent-cwd",
        str(workspace),
        "--startup-timeout",
        "5",
        "--timeout",
        "5",
        "--test-timeout",
        "30",
        "--cancel-prompt",
        "agy-acp-tck-cancel",
        "--report-json",
        str(report),
        "--",
        sys.executable,
        "-m",
        "agy_acp",
        "--agy-path",
        str(wrapper),
        "--prompt-timeout",
        "5",
    ]
    with (root / "stdout.log").open("wb") as stdout, (root / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=_safe_environment(root),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            return_code = process.wait(timeout=_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _terminate(process)
            raise RuntimeError("ACP TCK timed out") from None
        finally:
            _terminate(process)
        return return_code, stdout.tell(), stderr.tell()


def _object(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"ACP TCK report has invalid {field}")
    return cast(Mapping[str, Any], value)


def _registry_tiers() -> dict[str, str]:
    module = importlib.import_module("tck.v1.requirements")
    registry = getattr(module, "REGISTRY", None)
    if not isinstance(registry, Mapping):
        raise RuntimeError("ACP TCK registry is unavailable")
    tiers: dict[str, str] = {}
    for requirement_id, requirement in registry.items():
        tier = getattr(getattr(requirement, "tier", None), "value", None)
        if not isinstance(requirement_id, str) or not isinstance(tier, str):
            raise RuntimeError("ACP TCK registry is invalid")
        tiers[requirement_id] = tier
    mandatory = sum(tier == "MANDATORY" for tier in tiers.values())
    if len(tiers) != _EXPECTED_REQUIREMENTS or mandatory != _EXPECTED_MANDATORY:
        raise RuntimeError("ACP TCK registry has an unexpected shape")
    return tiers


def _validate_report(report: Path) -> tuple[int, int]:
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("ACP TCK report could not be read") from None
    root = _object(payload, "root")
    if root.get("tck_version") != _TCK_VERSION:
        raise RuntimeError("ACP TCK report has an unexpected TCK version")
    if root.get("protocol_version") != 1:
        raise RuntimeError("ACP TCK report has an unexpected protocol version")
    if root.get("schema_revision") != _SCHEMA_REVISION:
        raise RuntimeError("ACP TCK report has an unexpected schema revision")

    verdict = _object(root.get("verdict"), "verdict")
    if verdict.get("conformant") is not True:
        raise RuntimeError("ACP TCK verdict is not conformant")
    if verdict.get("blocked_by_auth") is not False:
        raise RuntimeError("ACP TCK was blocked by authentication")
    if verdict.get("blocked_by_version_mismatch") is not False:
        raise RuntimeError("ACP TCK was blocked by version negotiation")

    requirements = root.get("requirements")
    if not isinstance(requirements, list):
        raise RuntimeError("ACP TCK report has invalid requirements")
    expected_tiers = _registry_tiers()
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in requirements:
        requirement = _object(item, "requirement")
        requirement_id = requirement.get("id")
        tier = requirement.get("tier")
        status = requirement.get("status")
        if not isinstance(requirement_id, str) or requirement_id in by_id:
            raise RuntimeError("ACP TCK report has invalid requirement identities")
        if tier not in _TIERS or status not in _STATUSES:
            raise RuntimeError("ACP TCK report has invalid requirement state")
        by_id[requirement_id] = requirement
    if by_id.keys() != expected_tiers.keys():
        raise RuntimeError("ACP TCK report requirement set is not exact")

    computed_counts = {tier: {status: 0 for status in _STATUSES} for tier in _TIERS}
    mandatory = 0
    for requirement_id, requirement in by_id.items():
        tier = cast(str, requirement["tier"])
        status = cast(str, requirement["status"])
        if tier != expected_tiers[requirement_id]:
            raise RuntimeError("ACP TCK report requirement tier is not exact")
        computed_counts[tier][status] += 1
        if tier == "MANDATORY":
            mandatory += 1
            if status != "PASS":
                raise RuntimeError(f"ACP TCK mandatory requirement did not pass: {requirement_id}")
    if verdict.get("tier_counts") != computed_counts:
        raise RuntimeError("ACP TCK verdict counts are inconsistent")

    for requirement_id in _REQUIRED_CAPABILITIES:
        if by_id[requirement_id].get("status") != "PASS":
            raise RuntimeError(f"ACP TCK advertised capability did not pass: {requirement_id}")
    return mandatory, len(_REQUIRED_CAPABILITIES)


def run(report: Path) -> None:
    if not report.is_absolute() or not report.parent.is_dir():
        raise ValueError("report path must be absolute with an existing parent directory")
    with tempfile.TemporaryDirectory(prefix="agy-acp-tck-", dir=_scratch_root()) as temporary:
        root = Path(temporary)
        return_code, stdout_bytes, stderr_bytes = _execute_tck(root, report)
    if return_code != 0:
        raise RuntimeError(
            f"ACP TCK failed with exit code {return_code}; "
            f"stdout_bytes={stdout_bytes}; stderr_bytes={stderr_bytes}"
        )
    mandatory, capabilities = _validate_report(report)
    print(
        f"ACP TCK {_TCK_VERSION} passed: mandatory={mandatory}; "
        f"advertised_capabilities={capabilities}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the pinned experimental ACP v1 TCK")
    parser.add_argument("--report-json", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        run(arguments.report_json)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"tck_smoke: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
