import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_PROJECT_NAME = "agy-acp"
_DISTRIBUTION_NAME = "agy_acp"
_MAX_RESPONSE_BYTES = 1024 * 1024
_QUERY_TIMEOUT_SECONDS = 30
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"\d+\.\d+\.\d+")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _local_distributions(directory: Path, version: str) -> dict[str, Path]:
    if not _VERSION.fullmatch(version):
        raise RuntimeError("Release version is invalid")
    if not directory.is_dir():
        raise RuntimeError("Distribution directory is missing")
    expected = {
        f"{_DISTRIBUTION_NAME}-{version}-py3-none-any.whl",
        f"{_DISTRIBUTION_NAME}-{version}.tar.gz",
    }
    files = {path.name: path for path in directory.iterdir() if path.is_file()}
    if set(files) != expected:
        raise RuntimeError("Distribution directory is not exact")
    return files


def _parse_published_hashes(payload: object, version: str) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise RuntimeError("PyPI release response is invalid")
    response = cast(Mapping[object, object], payload)
    info = response.get("info")
    if not isinstance(info, Mapping) or info.get("version") != version:
        raise RuntimeError("PyPI release identity is invalid")
    urls = response.get("urls")
    if not isinstance(urls, list):
        raise RuntimeError("PyPI release files are invalid")

    published: dict[str, str] = {}
    for item in urls:
        if not isinstance(item, Mapping):
            raise RuntimeError("PyPI release file is invalid")
        filename = item.get("filename")
        digests = item.get("digests")
        if not isinstance(filename, str) or not isinstance(digests, Mapping):
            raise RuntimeError("PyPI release file identity is invalid")
        sha256 = digests.get("sha256")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise RuntimeError("PyPI release file hash is invalid")
        if filename in published:
            raise RuntimeError("PyPI release contains a duplicate filename")
        published[filename] = sha256
    return published


def _fetch_published_hashes(version: str) -> dict[str, str]:
    endpoint = (
        f"https://pypi.org/pypi/{quote(_PROJECT_NAME, safe='')}/{quote(version, safe='')}/json"
    )
    request = Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "agy-acp-release-workflow",
        },
    )
    try:
        with urlopen(request, timeout=_QUERY_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            return {}
        raise RuntimeError("PyPI release query failed") from error
    except URLError as error:
        raise RuntimeError("PyPI release query failed") from error
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("PyPI release response exceeded its limit")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("PyPI release response is not valid JSON") from error
    return _parse_published_hashes(payload, version)


def _prepare_publish(
    source: Path,
    destination: Path,
    version: str,
    published: Mapping[str, str],
) -> tuple[str, ...]:
    local = _local_distributions(source, version)
    unexpected = set(published) - set(local)
    if unexpected:
        raise RuntimeError("PyPI release contains an unexpected distribution")

    missing: list[str] = []
    for filename, path in sorted(local.items()):
        local_hash = _sha256(path)
        published_hash = published.get(filename)
        if published_hash is None:
            missing.append(filename)
        elif published_hash != local_hash:
            raise RuntimeError("PyPI distribution hash does not match the validated artifact")

    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise RuntimeError("Publication staging directory is not empty")
    else:
        destination.mkdir(parents=True)
    for filename in missing:
        target = destination / filename
        shutil.copyfile(local[filename], target)
        if _sha256(target) != _sha256(local[filename]):
            raise RuntimeError("Staged distribution hash is invalid")
    return tuple(missing)


def _write_github_outputs(missing: Sequence[str]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output is None:
        return
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"publish-needed={'true' if missing else 'false'}\n")
        stream.write(f"missing-count={len(missing)}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        published = _fetch_published_hashes(arguments.version)
        missing = _prepare_publish(
            arguments.dist_dir,
            arguments.output_dir,
            arguments.version,
            published,
        )
        _write_github_outputs(missing)
    except (OSError, RuntimeError):
        print("PyPI publication plan: failed", file=sys.stderr)
        return 1
    print(f"PyPI publication plan: {len(missing)} distribution(s) require upload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
