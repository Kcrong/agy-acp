import json
from pathlib import Path

import pytest

from scripts import prepare_pypi_publish
from scripts.prepare_pypi_publish import (
    _parse_published_hashes,
    _prepare_publish,
    _sha256,
    main,
)

_VERSION = "1.2.3"
_WHEEL = f"agy_acp-{_VERSION}-py3-none-any.whl"
_SDIST = f"agy_acp-{_VERSION}.tar.gz"


def _write_distributions(directory: Path) -> dict[str, Path]:
    directory.mkdir()
    wheel = directory / _WHEEL
    sdist = directory / _SDIST
    wheel.write_bytes(b"validated wheel")
    sdist.write_bytes(b"validated sdist")
    return {_WHEEL: wheel, _SDIST: sdist}


def _release_payload(files: dict[str, str]) -> object:
    return json.loads(
        json.dumps(
            {
                "info": {"version": _VERSION},
                "urls": [
                    {"filename": filename, "digests": {"sha256": digest}}
                    for filename, digest in files.items()
                ],
            }
        )
    )


def test_parse_published_hashes_reads_release_files() -> None:
    hashes = {_WHEEL: "a" * 64, _SDIST: "b" * 64}

    assert _parse_published_hashes(_release_payload(hashes), _VERSION) == hashes


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"info": {"version": "1.2.4"}, "urls": []},
        {"info": {"version": _VERSION}, "urls": "invalid"},
        {
            "info": {"version": _VERSION},
            "urls": [{"filename": _WHEEL, "digests": {"sha256": "invalid"}}],
        },
    ],
)
def test_parse_published_hashes_rejects_invalid_response(payload: object) -> None:
    with pytest.raises(RuntimeError):
        _parse_published_hashes(payload, _VERSION)


def test_prepare_publish_stages_both_files_for_new_release(tmp_path: Path) -> None:
    source = tmp_path / "source"
    files = _write_distributions(source)
    destination = tmp_path / "pending"

    missing = _prepare_publish(source, destination, _VERSION, {})

    assert missing == (_WHEEL, _SDIST)
    assert {path.name for path in destination.iterdir()} == set(files)


def test_prepare_publish_stages_only_verified_missing_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    files = _write_distributions(source)
    destination = tmp_path / "pending"
    published = {_WHEEL: _sha256(files[_WHEEL])}

    assert _prepare_publish(source, destination, _VERSION, published) == (_SDIST,)
    assert [path.name for path in destination.iterdir()] == [_SDIST]


def test_prepare_publish_is_noop_when_release_matches(tmp_path: Path) -> None:
    source = tmp_path / "source"
    files = _write_distributions(source)
    destination = tmp_path / "pending"
    published = {filename: _sha256(path) for filename, path in files.items()}

    assert _prepare_publish(source, destination, _VERSION, published) == ()
    assert list(destination.iterdir()) == []


def test_prepare_publish_rejects_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_distributions(source)

    with pytest.raises(RuntimeError, match="does not match"):
        _prepare_publish(source, tmp_path / "pending", _VERSION, {_WHEEL: "0" * 64})


def test_prepare_publish_rejects_unexpected_remote_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_distributions(source)

    with pytest.raises(RuntimeError, match="unexpected distribution"):
        _prepare_publish(source, tmp_path / "pending", _VERSION, {"other.whl": "0" * 64})


def test_main_records_whether_publication_is_needed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_distributions(source)
    github_output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setattr(prepare_pypi_publish, "_fetch_published_hashes", lambda _version: {})

    assert (
        main(
            [
                "--version",
                _VERSION,
                "--dist-dir",
                str(source),
                "--output-dir",
                str(tmp_path / "pending"),
            ]
        )
        == 0
    )
    assert github_output.read_text(encoding="utf-8") == "publish-needed=true\nmissing-count=2\n"


def test_main_hides_failure_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "private-source"
    _write_distributions(source)
    monkeypatch.setattr(
        prepare_pypi_publish,
        "_fetch_published_hashes",
        lambda _version: {_WHEEL: "0" * 64},
    )

    assert (
        main(
            [
                "--version",
                _VERSION,
                "--dist-dir",
                str(source),
                "--output-dir",
                str(tmp_path / "pending"),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "PyPI publication plan: failed\n"
    assert str(source) not in captured.err
