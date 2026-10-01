from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parents[2]


def load_project() -> dict[str, Any]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject:
        return tomllib.load(pyproject)


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
    assert project["urls"] == {
        "Repository": "https://github.com/Kcrong/agy-acp",
        "Issues": "https://github.com/Kcrong/agy-acp/issues",
        "Security": "https://github.com/Kcrong/agy-acp/security/policy",
    }


def test_readme_remains_concise_and_scope_limited() -> None:
    lines = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith("## ")] == [
        "## Purpose",
        "## Installation",
        "## Implementation overview",
    ]
    assert len(lines) <= 40


def test_sdist_public_file_allowlist_is_explicit() -> None:
    include = load_project()["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert include == [
        "src/agy_acp",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
    ]
    assert (PROJECT_ROOT / "SECURITY.md").is_file()
    assert (PROJECT_ROOT / "CONTRIBUTING.md").is_file()
