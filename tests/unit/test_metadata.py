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


def test_local_planning_files_are_ignored() -> None:
    ignored = set((PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {"north_star.md", "roadmap.md", "tasks.md"} <= ignored
