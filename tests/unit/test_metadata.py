from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import Any

from agy_acp import __version__

PROJECT_ROOT = Path(__file__).parents[2]


def load_project() -> dict[str, Any]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject:
        return tomllib.load(pyproject)


def test_project_version_is_the_runtime_source() -> None:
    expected = load_project()["project"]["version"]
    assert expected == version("agy-acp") == __version__


def test_supported_python_range_is_exact() -> None:
    project = load_project()["project"]
    assert project["requires-python"] == ">=3.13,<3.15"


def test_runtime_dependencies_are_exact() -> None:
    project = load_project()["project"]
    assert project["dependencies"] == ["agent-client-protocol==0.12.1"]


def test_console_entry_point_is_stable() -> None:
    project = load_project()["project"]
    assert project["scripts"] == {"agy-acp": "agy_acp.cli:main"}


def test_build_backend_is_exact_and_available_without_isolation() -> None:
    pyproject = load_project()
    assert pyproject["build-system"]["requires"] == ["hatchling==1.32.4"]
    assert "hatchling==1.32.4" in pyproject["dependency-groups"]["dev"]


def test_local_planning_files_are_ignored() -> None:
    ignored = set((PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {"north_star.md", "roadmap.md", "tasks.md"} <= ignored


def test_public_readme_and_project_urls_are_explicit() -> None:
    project = load_project()["project"]
    assert project["readme"] == "README.md"
    assert project["license"] == "Apache-2.0"
    assert project["keywords"] == ["acp", "agent-client-protocol", "antigravity", "adapter"]
    assert project["classifiers"] == [
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: MacOS",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Typing :: Typed",
    ]
    assert all("Windows" not in classifier for classifier in project["classifiers"])
    assert project["urls"] == {
        "Repository": "https://github.com/Kcrong/agy-acp",
        "Issues": "https://github.com/Kcrong/agy-acp/issues",
        "Security": "https://github.com/Kcrong/agy-acp/security/policy",
    }


def test_readme_badges_are_dynamic_and_scoped() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    expected = [
        "https://github.com/Kcrong/agy-acp/actions/workflows/ci.yml/badge.svg",
        "https://img.shields.io/pypi/v/agy-acp?include_prereleases",
        "https://img.shields.io/pypi/pyversions/agy-acp",
        "https://img.shields.io/pypi/l/agy-acp",
    ]
    for badge in expected:
        assert readme.count(badge) == 1
    assert "https://pypi.org/project/agy-acp/" in readme
    assert "](./LICENSE)" in readme


def test_readme_remains_concise_and_scope_limited() -> None:
    lines = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith("## ")] == [
        "## Purpose",
        "## Requirements",
        "## Installation",
        "## Privacy and support",
    ]
    assert len(lines) <= 50


def test_readme_documents_verified_pipx_flows() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "pipx install agy-acp" in readme
    assert "pipx run agy-acp --help" in readme
    expected_version = load_project()["project"]["version"]
    assert f'pipx run --spec "agy-acp=={expected_version}" agy-acp --help' in readme
    assert "Python 3.13 or 3.14 executable with `--python`" in readme
    assert "It does not install or authenticate the required `agy` backend." in readme
    assert "Until the first PyPI release" not in readme


def test_sdist_public_file_allowlist_is_explicit() -> None:
    sdist = load_project()["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert sdist["include"] == [
        "src/agy_acp",
        ".gitignore",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
    ]
    assert (PROJECT_ROOT / "SECURITY.md").is_file()
    assert (PROJECT_ROOT / "CONTRIBUTING.md").is_file()
