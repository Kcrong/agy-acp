from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_runs_tests_only_for_ready_pull_requests() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "\n  push:" not in workflow
    assert "  pull_request:\n" in workflow
    for event in (
        "converted_to_draft",
        "opened",
        "ready_for_review",
        "reopened",
        "synchronize",
    ):
        assert f"      - {event}\n" in workflow
    assert "    if: ${{ github.event.pull_request.draft == false }}\n" in workflow


def test_ci_required_check_names_are_stable() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "name: ${{ matrix.os }} / Python ${{ matrix.python-version }}" in workflow
    assert workflow.count("          - ubuntu-latest") == 1
    assert workflow.count("          - macos-latest") == 1
    assert workflow.count('          - "3.13"') == 1
    assert workflow.count('          - "3.14"') == 1
