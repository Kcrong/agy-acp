from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.tck_smoke import (
    _EXPECTED_MANDATORY,
    _REQUIRED_CAPABILITIES,
    _SCHEMA_REVISION,
    _STATUSES,
    _TCK_VERSION,
    _TIERS,
    _registry_tiers,
    _validate_report,
)

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="The pinned ACP TCK requires Python 3.14",
)


def valid_report() -> dict[str, Any]:
    tiers = _registry_tiers()
    requirements = [
        {"id": requirement_id, "tier": tier, "status": "PASS"}
        for requirement_id, tier in tiers.items()
    ]
    counts = {tier: {status: 0 for status in _STATUSES} for tier in _TIERS}
    for requirement in requirements:
        counts[requirement["tier"]][requirement["status"]] += 1
    return {
        "tck_version": _TCK_VERSION,
        "protocol_version": 1,
        "schema_revision": _SCHEMA_REVISION,
        "requirements": requirements,
        "verdict": {
            "conformant": True,
            "blocked_by_auth": False,
            "blocked_by_version_mismatch": False,
            "tier_counts": counts,
        },
    }


def write_report(tmp_path: Path, payload: dict[str, Any]) -> Path:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    return report


def test_validate_report_accepts_exact_registry(tmp_path: Path) -> None:
    assert _validate_report(write_report(tmp_path, valid_report())) == (
        _EXPECTED_MANDATORY,
        len(_REQUIRED_CAPABILITIES),
    )


def test_validate_report_rejects_missing_mandatory_requirement(tmp_path: Path) -> None:
    payload = valid_report()
    requirements = payload["requirements"]
    assert isinstance(requirements, list)
    removed = next(item for item in requirements if item["tier"] == "MANDATORY")
    requirements.remove(removed)

    with pytest.raises(RuntimeError, match="requirement set is not exact"):
        _validate_report(write_report(tmp_path, payload))


def test_validate_report_rejects_modified_tier(tmp_path: Path) -> None:
    payload = valid_report()
    requirements = payload["requirements"]
    assert isinstance(requirements, list)
    modified = copy.deepcopy(requirements[0])
    modified["tier"] = "ADVISORY" if modified["tier"] != "ADVISORY" else "MANDATORY"
    requirements[0] = modified

    with pytest.raises(RuntimeError, match="requirement tier is not exact"):
        _validate_report(write_report(tmp_path, payload))


def test_validate_report_rejects_inconsistent_counts(tmp_path: Path) -> None:
    payload = valid_report()
    verdict = payload["verdict"]
    assert isinstance(verdict, dict)
    counts = verdict["tier_counts"]
    assert isinstance(counts, dict)
    counts["MANDATORY"]["PASS"] = 0

    with pytest.raises(RuntimeError, match="verdict counts are inconsistent"):
        _validate_report(write_report(tmp_path, payload))
