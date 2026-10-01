from __future__ import annotations

import pytest

from agy_acp.cli import build_parser, main


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


def test_hidden_server_options_parse_without_expanding_help() -> None:
    arguments = build_parser().parse_args(
        [
            "--agy-path",
            "/absolute/agy",
            "--prompt-timeout",
            "12.5",
            "--max-line-bytes",
            "8192",
        ]
    )
    assert arguments.agy_path == "/absolute/agy"
    assert arguments.prompt_timeout == 12.5
    assert arguments.max_line_bytes == 8192


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--prompt-timeout", "nan"),
        ("--prompt-timeout", "inf"),
        ("--prompt-timeout", "0"),
        ("--max-line-bytes", "0"),
    ],
)
def test_hidden_server_options_require_positive_finite_values(
    option: str,
    value: str,
) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args([option, value])
    assert raised.value.code == 2
