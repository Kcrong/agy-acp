from __future__ import annotations

import pytest

from scripts.git_install_smoke import build_requirement


def test_build_requirement_accepts_public_github_exact_sha() -> None:
    revision = "a" * 40
    assert (
        build_requirement("https://github.com/Kcrong/agy-acp.git", revision)
        == f"git+https://github.com/Kcrong/agy-acp.git@{revision}"
    )


@pytest.mark.parametrize(
    ("repository", "revision"),
    [
        ("https://example.com/Kcrong/agy-acp.git", "a" * 40),
        ("https://token@github.com/Kcrong/agy-acp.git", "a" * 40),
        ("https://github.com/Kcrong/agy-acp", "a" * 40),
        ("https://github.com/Kcrong/agy-acp.git", "main"),
        ("https://github.com/Kcrong/agy-acp.git", "a" * 39),
    ],
)
def test_build_requirement_rejects_non_public_or_non_exact_input(
    repository: str,
    revision: str,
) -> None:
    with pytest.raises(ValueError, match="invalid"):
        build_requirement(repository, revision)
