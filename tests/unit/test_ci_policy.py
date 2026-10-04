from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def load_workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_trigger_covers_ready_pull_requests_and_main_pushes() -> None:
    workflow = load_workflow()
    trigger = workflow.split("\non:\n", maxsplit=1)[1].split("\npermissions:\n", maxsplit=1)[0]

    assert (
        trigger
        == """  pull_request:
    branches:
      - main
    types:
      - closed
      - converted_to_draft
      - opened
      - ready_for_review
      - reopened
      - synchronize
  push:
    branches:
      - main
"""
    )


def test_ci_concurrency_is_revision_scoped() -> None:
    workflow = load_workflow()
    jobs = workflow.split("\njobs:\n", maxsplit=1)[1]
    job_headers = [
        line.strip()[:-1]
        for line in jobs.splitlines()
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":")
    ]

    assert job_headers == ["test"]
    assert (
        """concurrency:
  group: ci-${{ github.workflow }}-${{ github.event_name == 'push' && github.sha || github.ref }}
  cancel-in-progress: true
"""
        in workflow
    )
    assert (
        "    if: ${{ github.event_name == 'push' || (github.event.action != 'closed' "
        "&& github.event.pull_request.draft == false) }}\n"
    ) in workflow


def test_ci_required_check_matrix_is_exact() -> None:
    workflow = load_workflow()
    matrix = workflow.split("      matrix:\n", maxsplit=1)[1].split("    steps:\n", maxsplit=1)[0]

    assert (
        matrix
        == """        os:
          - ubuntu-latest
          - macos-latest
        python-version:
          - "3.13"
          - "3.14"
"""
    )
    assert "include:" not in matrix
    assert "exclude:" not in matrix
    assert "    name: ${{ matrix.os }} / Python ${{ matrix.python-version }}\n" in workflow
