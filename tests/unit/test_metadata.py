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


def test_project_version_is_derived_from_vcs() -> None:
    project = load_project()["project"]
    assert project["dynamic"] == ["version"]
    assert "version" not in project
    assert version("agy-acp") == __version__


def test_supported_python_range_is_exact() -> None:
    project = load_project()["project"]
    assert project["requires-python"] == ">=3.13,<3.15"


def test_runtime_dependencies_are_exact() -> None:
    project = load_project()["project"]
    assert project["dependencies"] == ["agent-client-protocol==0.12.1"]


def test_console_entry_point_is_stable() -> None:
    project = load_project()["project"]
    assert project["scripts"] == {"agy-acp": "agy_acp.cli:main"}


def test_build_backend_and_vcs_version_source_are_exact() -> None:
    pyproject = load_project()
    assert pyproject["build-system"]["requires"] == [
        "hatchling==1.32.4",
        "hatch-vcs==0.5.0",
    ]
    assert "hatchling==1.32.4" in pyproject["dependency-groups"]["dev"]
    assert "hatch-vcs==0.5.0" in pyproject["dependency-groups"]["dev"]
    assert pyproject["tool"]["hatch"]["version"] == {
        "source": "vcs",
        "tag-pattern": r"^v(?P<version>\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)$",
    }


def test_local_planning_files_are_ignored() -> None:
    ignored = set((PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {"north_star.md", "roadmap.md", "tasks.md"} <= ignored


def test_public_readme_and_project_urls_are_explicit() -> None:
    project = load_project()["project"]
    assert project["readme"] == "README.md"
    assert project["license"] == "Apache-2.0"
    assert project["keywords"] == ["acp", "agent-client-protocol", "antigravity", "adapter"]
    assert project["classifiers"] == [
        "Development Status :: 5 - Production/Stable",
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


def test_readme_documents_usage_and_remains_scope_limited() -> None:
    lines = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith("## ")] == [
        "## Purpose",
        "## Requirements",
        "## Installation",
        "## Run the adapter",
        "## Configure an ACP client",
        "## Options",
        "## Supported ACP behavior",
        "## Troubleshooting",
        "## Privacy and support",
    ]
    assert len(lines) <= 170


def test_readme_documents_verified_install_and_run_flows() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "pipx install agy-acp" in readme
    assert "pipx run agy-acp --help" in readme
    assert 'pipx run --spec "agy-acp==X.Y.Z" agy-acp --help' in readme
    assert "Python 3.13 or 3.14 executable with `--python`" in readme
    assert "`pipx install` creates the isolated environment during installation" in readme
    assert "`pipx run` creates and caches a temporary environment on first use" in readme
    assert "It does not install or authenticate the required `agy` backend." in readme
    assert "agy --version" in readme
    assert "agy models" in readme
    assert "Until the first PyPI release" not in readme


def test_readme_client_command_examples_are_exact() -> None:
    import json
    import re

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", readme, flags=re.DOTALL)
    assert [json.loads(block) for block in blocks] == [
        {"command": "agy-acp", "args": []},
        {"command": "pipx", "args": ["run", "agy-acp"]},
    ]


def test_readme_public_cli_matches_generated_help() -> None:
    from agy_acp.cli import build_parser

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    help_text = build_parser().format_help()
    assert "usage: agy-acp [-h] [--version]" in help_text
    assert "-h, --help" in help_text
    assert "--version" in help_text
    for hidden_option in (
        "--agy-path",
        "--prompt-timeout",
        "--max-line-bytes",
        "--max-in-flight",
    ):
        assert hidden_option not in help_text
    assert "| `-h`, `--help` | Show command usage and exit. |" in readme
    assert "| `--version` | Print the installed `agy-acp` version and exit. |" in readme
    assert "no `--model` or `--effort` flags" in readme


def test_readme_documents_exact_session_and_support_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "models returned by `agy models`" in readme
    assert "reasoning effort" in readme
    assert "Offers `default` for the agy-selected model and unsuffixed concrete models" in readme
    assert "An unsuffixed concrete model keeps `effort` at `default`." in readme
    assert (
        "| `cwd` | Required existing absolute directory for a new or resumed session. |" in readme
    )
    assert "`session/new` requires a list (use `[]` when none)" in readme
    assert "`session/resume` may omit it" in readme
    assert "Model and effort changes apply from the next prompt" in readme
    assert "Restarting the adapter refreshes" in readme
    assert "Create, prompt, cancel, resume, and close independent sessions" in readme
    assert "`session/load`, HTTP/SSE/ACP MCP transports" in readme
    assert "not an interactive prompt" in readme
    assert "The adapter adds no telemetry, network listener, or credential store" in readme
    assert "The installed `agy` process is also inside the trusted boundary" in readme


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
