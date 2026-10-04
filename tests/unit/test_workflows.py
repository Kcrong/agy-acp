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


def test_ci_workflow_runs_on_ready_prs_and_main_and_is_sha_pinned() -> None:
    workflow = load_workflow("ci.yml")
    assert set(workflow["on"]) == {"pull_request", "push"}
    assert workflow["on"]["push"] == {"branches": ["main"]}
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
    assert "github.event_name == 'push'" in raw
    assert "github.event_name == 'push' && github.sha || github.ref" in raw
    assert "github.event.pull_request.draft == false" in raw
    assert "fetch-depth: 0" in raw
    assert "fetch-tags: true" in raw
    assert "UV_FROZEN" not in raw
    assert "run: uv lock --check" in raw
    assert "Stress Darwin MCP cleanup" in raw
    assert (
        "tests/integration/test_mcp_handoff.py::test_multiple_mcp_servers_and_concurrent_sessions_are_isolated"
        in raw
    )
    assert "windows" not in str(workflow).lower()


def test_pr_title_workflow_validates_conventional_commits_without_checkout() -> None:
    workflow = load_workflow("pr-title.yml")
    assert set(workflow["on"]) == {"pull_request_target"}
    assert workflow["permissions"] == {"pull-requests": "read"}
    references = action_references(workflow)
    assert references == [
        "amannn/action-semantic-pull-request@48f256284bd46cdaab1048c3721360e808335d50"
    ]
    assert all(_ACTION.fullmatch(reference) for reference in references)
    assert set(workflow["jobs"]["validate"]["steps"][0]["with"]["types"].splitlines()) == {
        "build",
        "chore",
        "ci",
        "docs",
        "feat",
        "fix",
        "perf",
        "refactor",
        "revert",
        "style",
        "test",
    }
    assert not any(reference.startswith("actions/checkout@") for reference in references)


def test_publish_workflow_is_ci_gated_automatic_and_sha_pinned() -> None:
    workflow = load_workflow("publish.yml")
    assert set(workflow["on"]) == {"workflow_run"}
    assert workflow["on"]["workflow_run"] == {
        "workflows": ["CI"],
        "types": ["completed"],
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "publish-pypi",
        "cancel-in-progress": False,
        "queue": "max",
    }
    tag = workflow["jobs"]["tag"]
    assert tag["permissions"] == {"contents": "write", "pull-requests": "read"}
    condition = tag["if"]
    for expected in (
        "workflow_run.conclusion == 'success'",
        "workflow_run.event == 'push'",
        "workflow_run.head_branch == 'main'",
        "workflow_run.head_repository.full_name == github.repository",
    ):
        assert expected in condition
    assert workflow["jobs"]["build"]["needs"] == "tag"
    prepare = workflow["jobs"]["prepare-publish"]
    assert set(prepare["needs"]) == {"tag", "build"}
    assert prepare["outputs"] == {"publish-needed": "${{ steps.plan.outputs.publish-needed }}"}
    publish = workflow["jobs"]["publish"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
    assert set(publish["needs"]) == {"tag", "build", "prepare-publish"}
    assert publish["if"] == "${{ needs.prepare-publish.outputs.publish-needed == 'true' }}"

    references = action_references(workflow)
    assert references
    assert all(_ACTION.fullmatch(reference) for reference in references)
    assert any(reference.startswith("mathieudutour/github-tag-action@") for reference in references)
    assert any(reference.startswith("actions/upload-artifact@") for reference in references)
    assert any(reference.startswith("actions/download-artifact@") for reference in references)
    assert any(reference.startswith("pypa/gh-action-pypi-publish@") for reference in references)

    raw = (PROJECT_ROOT / ".github" / "workflows" / "publish.yml").read_text()
    assert "workflow_dispatch" not in raw
    assert "github.event.workflow_run.head_sha" in raw
    assert 'git merge-base --is-ancestor "$RELEASE_COMMIT" origin/main' in raw
    assert (
        'if test "$latest_commit" != "$RELEASE_COMMIT" && \\\n'
        '            git merge-base --is-ancestor "$RELEASE_COMMIT" "$latest_commit"; then' in raw
    )
    assert 'git merge-base --is-ancestor "$latest_commit" "$RELEASE_COMMIT"' in raw
    assert "Commit is already included in a newer release tag" in raw
    assert '"repos/$GITHUB_REPOSITORY/commits/$commit/pulls"' in raw
    assert 'pull_request.get("merge_commit_sha") == commit' in raw
    assert 'pull_request.get("base", {}).get("ref") == "main"' in raw
    assert "if len(matches) != 1:" in raw
    assert (
        "expected_subject = f\"{pull_request.get('title')} (#{pull_request.get('number')})\"" in raw
    )
    assert "release commit subject does not match its validated PR title" in raw
    assert "steps.tags.outputs.releasable == 'true'" in raw
    assert raw.count("create_annotated_tag: true") == 2
    assert raw.count("previous_tag: ${{ steps.tags.outputs.previous_tag }}") == 2
    assert "dry_run: true" in raw
    assert "default_bump: false" in raw
    assert "custom_release_rules:" in raw
    assert "git tag --points-at HEAD" in raw
    assert 'test "$tag" = "$EXPECTED_TAG"' in raw
    assert 'git cat-file -t "$tag_ref"' in raw
    assert 'test "$(git rev-parse "$tag_ref^{commit}")" = "$RELEASE_COMMIT"' in raw
    assert "if: ${{ needs.tag.outputs.tag != '' }}" in raw
    assert "refs/tags/${{ needs.tag.outputs.tag }}" in raw
    assert "Build once from the release tag" in raw
    assert raw.count("python -m scripts.prepare_pypi_publish") == 2
    assert "needs.prepare-publish.outputs.publish-needed == 'true'" in raw
    assert "steps.plan.outputs.publish-needed == 'true'" in raw
    assert "skip-existing: false" in raw
    assert raw.count("id-token: write") == 1
    assert "password:" not in raw
    assert "user:" not in raw
    assert "windows" not in raw.lower()


def test_release_runbook_matches_automatic_trusted_publishing() -> None:
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
        "PR title",
        "Conventional Commits",
        "successful `main` CI run",
        "annotated `v<version>` tag",
        "PyPI Trusted Publishing",
    ):
        assert expected in runbook
