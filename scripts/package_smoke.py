from __future__ import annotations

import argparse
import contextlib
import email.policy
import json
import os
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Mapping, Sequence
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, cast

_MAX_OUTPUT_BYTES = 64 * 1024
_TIMEOUT_SECONDS = 180
_PROJECT_ROOT = Path(__file__).parents[1]


def _project() -> Mapping[str, Any]:
    with (_PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        payload = tomllib.load(stream)
    project = payload.get("project")
    if not isinstance(project, Mapping):
        raise RuntimeError("Project metadata is missing")
    return cast(Mapping[str, Any], project)


def _scratch_root() -> Path:
    for name in ("KIROCREW_SCRATCH", "RUNNER_TEMP"):
        value = os.environ.get(name)
        if value:
            root = Path(value)
            if root.is_dir():
                return root
            raise RuntimeError(f"{name} does not name an existing directory")
    return Path(tempfile.gettempdir())


def _member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise RuntimeError("Distribution contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError("Distribution contains an unsafe path")
    return path


def _source_files() -> dict[str, Path]:
    source_root = _PROJECT_ROOT / "src"
    return {
        path.relative_to(source_root).as_posix(): path
        for path in sorted((source_root / "agy_acp").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _distribution_files(directory: Path) -> tuple[Path, Path]:
    wheels = list(directory.glob("*.whl"))
    sdists = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("Distribution directory must contain exactly one wheel and one sdist")
    return wheels[0], sdists[0]


def _validate_wheel(path: Path, version: str) -> bytes:
    source_files = _source_files()
    dist_info = f"agy_acp-{version}.dist-info"
    generated = {
        f"{dist_info}/METADATA",
        f"{dist_info}/RECORD",
        f"{dist_info}/WHEEL",
        f"{dist_info}/entry_points.txt",
        f"{dist_info}/licenses/LICENSE",
    }
    expected = {*source_files, *generated}
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = {member.filename for member in members}
        if len(names) != len(members) or names != expected:
            raise RuntimeError("Wheel manifest is not exact")
        for member in members:
            _member_path(member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError("Wheel contains a symbolic link")
        for name, source in source_files.items():
            if archive.read(name) != source.read_bytes():
                raise RuntimeError("Wheel package file differs from source")
        if (
            archive.read(f"{dist_info}/licenses/LICENSE")
            != (_PROJECT_ROOT / "LICENSE").read_bytes()
        ):
            raise RuntimeError("Wheel license differs from source")
        if archive.read("agy_acp/py.typed") != b"":
            raise RuntimeError("Wheel py.typed marker is invalid")
        entry_points = archive.read(f"{dist_info}/entry_points.txt").decode("utf-8")
        if entry_points.strip() != "[console_scripts]\nagy-acp = agy_acp.cli:main":
            raise RuntimeError("Wheel console entry point is invalid")
        wheel_metadata = archive.read(f"{dist_info}/WHEEL").decode("utf-8")
        if (
            "Root-Is-Purelib: true" not in wheel_metadata
            or "Tag: py3-none-any" not in wheel_metadata
        ):
            raise RuntimeError("Wheel compatibility tag is invalid")
        return archive.read(f"{dist_info}/METADATA")


def _validate_sdist(path: Path, version: str) -> bytes:
    prefix = f"agy_acp-{version}"
    source_files = _source_files()
    public_root_files = {
        ".gitignore",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "SECURITY.md",
        "pyproject.toml",
        "uv.lock",
    }
    expected = {f"{prefix}/PKG-INFO"}
    expected.update(f"{prefix}/{name}" for name in public_root_files)
    expected.update(f"{prefix}/src/{name}" for name in source_files)
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if len(names) != len(members) or names != expected:
            raise RuntimeError("Sdist manifest is not exact")
        for member in members:
            _member_path(member.name)
            if not member.isfile():
                raise RuntimeError("Sdist contains a non-regular entry")
        for name in public_root_files:
            root_stream = archive.extractfile(f"{prefix}/{name}")
            if root_stream is None or root_stream.read() != (_PROJECT_ROOT / name).read_bytes():
                raise RuntimeError("Sdist root file differs from source")
        for name, source in source_files.items():
            source_stream = archive.extractfile(f"{prefix}/src/{name}")
            if source_stream is None or source_stream.read() != source.read_bytes():
                raise RuntimeError("Sdist package file differs from source")
        metadata = archive.extractfile(f"{prefix}/PKG-INFO")
        if metadata is None:
            raise RuntimeError("Sdist metadata is missing")
        return metadata.read()


def _validate_metadata(raw: bytes, project: Mapping[str, Any]) -> None:
    metadata = BytesParser(policy=email.policy.default).parsebytes(raw)
    expected = {
        "Name": project["name"],
        "Version": project["version"],
        "License-Expression": project["license"],
    }
    for name, value in expected.items():
        if metadata.get(name) != value:
            raise RuntimeError(f"Distribution metadata has an unexpected {name}")
    requires_python = project["requires-python"]
    if not isinstance(requires_python, str) or metadata.get("Requires-Python") != ",".join(
        sorted(requires_python.split(","))
    ):
        raise RuntimeError("Distribution metadata has an unexpected Requires-Python")
    if metadata.get_all("Requires-Dist", []) != project["dependencies"]:
        raise RuntimeError("Distribution dependencies are not exact")
    if metadata.get_all("Classifier", []) != project["classifiers"]:
        raise RuntimeError("Distribution classifiers are not exact")
    expected_urls = [f"{name}, {url}" for name, url in project["urls"].items()]
    if metadata.get_all("Project-URL", []) != expected_urls:
        raise RuntimeError("Distribution project URLs are not exact")
    keywords = metadata.get("Keywords")
    if keywords != ",".join(sorted(project["keywords"])):
        raise RuntimeError("Distribution keywords are not exact")


def _safe_environment(root: Path) -> dict[str, str]:
    home = root / "home"
    cache = root / "cache"
    temporary = root / "tmp"
    for directory in (home, cache, temporary):
        directory.mkdir()
    environment = {
        "HOME": str(home),
        "NO_COLOR": "1",
        "PATH": os.defpath,
        "PIP_CACHE_DIR": str(cache),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "TMPDIR": str(temporary),
    }
    for name in ("LANG", "LC_ALL"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _run(command: Sequence[str], *, root: Path, environment: Mapping[str, str]) -> str:
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        raise RuntimeError("Package installation smoke timed out") from None
    if len(stdout) > _MAX_OUTPUT_BYTES or len(stderr) > _MAX_OUTPUT_BYTES:
        raise RuntimeError("Package installation smoke output exceeded its limit")
    if process.returncode != 0:
        raise RuntimeError(
            f"Package installation smoke failed with exit code {process.returncode}; "
            f"stdout_bytes={len(stdout)}; stderr_bytes={len(stderr)}"
        )
    return stdout.decode("utf-8", errors="strict").strip()


def _validate_clean_install(wheel: Path, version: str) -> None:
    if os.name != "posix":
        raise RuntimeError("Package smoke supports the project Linux/macOS scope only")
    with tempfile.TemporaryDirectory(prefix="agy-acp-package-", dir=_scratch_root()) as temporary:
        root = Path(temporary)
        environment = _safe_environment(root)
        venv = root / "venv"
        _run([sys.executable, "-m", "venv", str(venv)], root=root, environment=environment)
        python = venv / "bin" / "python"
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--no-input",
                str(wheel),
            ],
            root=root,
            environment=environment,
        )
        _run([str(python), "-m", "pip", "check"], root=root, environment=environment)
        observed = _run(
            [
                str(python),
                "-c",
                "import agy_acp, json; print(json.dumps({'version': agy_acp.__version__}))",
            ],
            root=root,
            environment=environment,
        )
        if json.loads(observed) != {"version": version}:
            raise RuntimeError("Installed package version is inconsistent")
        console = venv / "bin" / "agy-acp"
        if _run([str(console), "--version"], root=root, environment=environment) != (
            f"agy-acp {version}"
        ):
            raise RuntimeError("Installed console version is inconsistent")
        if not _run([str(console), "--help"], root=root, environment=environment).startswith(
            "usage: agy-acp"
        ):
            raise RuntimeError("Installed console help is invalid")


def run(directory: Path) -> None:
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("distribution path must be an existing absolute directory")
    project = _project()
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("Project version is invalid")
    wheel, sdist = _distribution_files(directory)
    wheel_metadata = _validate_wheel(wheel, version)
    sdist_metadata = _validate_sdist(sdist, version)
    _validate_metadata(wheel_metadata, project)
    _validate_metadata(sdist_metadata, project)
    if wheel_metadata != sdist_metadata:
        raise RuntimeError("Wheel and sdist metadata differ")
    _validate_clean_install(wheel, version)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate built release distributions")
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args(argv)
    try:
        run(arguments.directory)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        print("package smoke: failed", file=sys.stderr)
        return 1
    print("package smoke: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
