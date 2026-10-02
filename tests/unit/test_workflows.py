from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).parents[2]
_ACTION = re.compile(r"[^/@]+/[^/@]+@[0-9a-f]{40}")


def load_workflow(name: str) -> dict[str, Any]:
    payload = yaml.safe_load((PROJECT_ROOT / ".github" / "workflows" / name).read_text())
    assert isinstance(payload, dict)
    raw = cast(dict[object, Any], payload)
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if key is True:
            result["on"] = value
        else:
            assert isinstance(key, str)
            result[key] = value
    return result


def action_references(workflow: dict[str, Any]) -> list[str]:
    references: list[str] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            reference = step.get("uses")
            if reference is not None:
                assert isinstance(reference, str)
                references.append(reference)
    return references


def test_ci_workflow_is_ready_pr_only_and_sha_pinned() -> None:
    workflow = load_workflow("ci.yml")
    assert set(workflow["on"]) == {"pull_request"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["test"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest"],
        "python-version": ["3.13", "3.14"],
    }
    references = action_references(workflow)
    assert references
    assert all(_ACTION.fullmatch(reference) for reference in references)
    assert any(reference.startswith("actions/upload-artifact@") for reference in references)
    raw = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "UV_FROZEN" not in raw
    assert "run: uv lock --check" in raw
    assert "Stress Darwin MCP cleanup" in raw
    assert (
        "tests/integration/test_mcp_handoff.py::test_multiple_mcp_servers_and_concurrent_sessions_are_isolated"
        in raw
    )
    assert "windows" not in str(workflow).lower()


def test_publish_workflow_is_manual_validated_and_sha_pinned() -> None:
    workflow = load_workflow("publish.yml")
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"]["release_tag"] == {
        "description": "Existing vX.Y.Z or prerelease tag on main to publish",
        "required": True,
        "type": "string",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "publish-pypi",
        "cancel-in-progress": False,
    }
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]
    assert matrix == {
        "os": ["ubuntu-latest", "macos-latest"],
        "python-version": ["3.13", "3.14"],
    }
    publish = workflow["jobs"]["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"] == {"id-token": "write"}
    assert set(publish["needs"]) == {"validate", "build"}

    references = action_references(workflow)
    assert references
    assert all(_ACTION.fullmatch(reference) for reference in references)
    assert any(reference.startswith("actions/upload-artifact@") for reference in references)
    assert any(reference.startswith("actions/download-artifact@") for reference in references)
    assert any(reference.startswith("pypa/gh-action-pypi-publish@") for reference in references)

    raw = (PROJECT_ROOT / ".github" / "workflows" / "publish.yml").read_text()
    assert "${{ secrets." not in raw
    assert "password:" not in raw
    assert "user:" not in raw
    assert "UV_FROZEN" not in raw
    assert raw.count("run: uv lock --check") == 2
    assert "Stress Darwin MCP cleanup" in raw
    assert "ref: refs/tags/${{ inputs.release_tag }}" in raw
    assert "(?:(?:a|b|rc)" in raw
    assert 'git show-ref --verify --quiet "$tag_ref"' in raw
    assert 'git cat-file -t "$tag_ref"' in raw
    assert "WORKFLOW_REF: ${{ github.ref }}" in raw
    assert raw.count("id-token: write") == 1
    assert "windows" not in raw.lower()
    assert "workflow_dispatch" in raw


def test_release_runbook_matches_pending_publisher_identity() -> None:
    runbook = (PROJECT_ROOT / "docs" / "releasing.md").read_text()
    for expected in (
        "PyPI project name: `agy-acp`",
        "Owner: `Kcrong`",
        "Repository name: `agy-acp`",
        "Workflow name: `publish.yml`",
        "Environment name: `pypi`",
        "Protected branches only",
        "`Kcrong` as the sole release reviewer",
        "Prevent self-review** disabled",
        "unrestricted as `(Any)`",
        "publication trust boundary",
    ):
        assert expected in runbook
