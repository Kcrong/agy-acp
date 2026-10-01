from __future__ import annotations

import pytest

from agy_acp.cli import main


def test_version_flag_reports_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])

    assert raised.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == "agy-acp 0.1.0\n"
    assert captured.err == ""


def test_help_is_concise(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])

    assert raised.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("usage: agy-acp")
    assert "ACP v1 adapter for the Antigravity CLI" in captured.out
    assert len(captured.out.splitlines()) <= 12
    assert captured.err == ""


def test_no_arguments_fails_closed_without_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "agy-acp: the ACP server is not implemented yet\n"
