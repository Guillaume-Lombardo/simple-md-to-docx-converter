"""Tests for the committed Python quality policy."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from pytest_mock import MockerFixture

from scripts.ci import check_branch_coverage, pytest_branch_coverage
from scripts.ci.check_changed_coverage import (
    CoverageCheckError,
    calculate_changed_coverage,
    load_coverage,
    main,
    read_changed_lines,
)


def _file_coverage(
    *,
    executed: list[int],
    missing: list[int],
    excluded: list[int] | None = None,
    executed_branches: list[list[int]] | None = None,
    missing_branches: list[list[int]] | None = None,
) -> dict[str, Any]:
    excluded = excluded or []
    executed_branches = executed_branches or []
    missing_branches = missing_branches or []
    return {
        "executed_lines": executed,
        "missing_lines": missing,
        "excluded_lines": excluded,
        "executed_branches": executed_branches,
        "missing_branches": missing_branches,
        "functions": {},
        "classes": {},
        "summary": {
            "covered_lines": len(executed),
            "missing_lines": len(missing),
            "excluded_lines": len(excluded),
            "num_statements": len(executed) + len(missing),
            "covered_branches": len(executed_branches),
            "missing_branches": len(missing_branches),
            "num_branches": len(executed_branches) + len(missing_branches),
            "num_partial_branches": 0,
        },
    }


def _branch_report(*, covered: int, branches: int) -> dict[str, Any]:
    executed_branches = [[line, line + 1] for line in range(1, covered + 1)]
    missing_branches = [[line, line + 1] for line in range(covered + 1, branches + 1)]
    return {
        "meta": {"branch_coverage": True},
        "totals": {
            "covered_branches": covered,
            "missing_branches": branches - covered,
            "num_branches": branches,
            "num_partial_branches": 0,
        },
        "files": {
            "src/markweave/service.py": _file_coverage(
                executed=[1],
                missing=[],
                executed_branches=executed_branches,
                missing_branches=missing_branches,
            )
        },
    }


@pytest.mark.unit
def test_committed_quality_configuration_enforces_required_tools() -> None:
    """The checked-in policy fixes every T05 quality invariant in one configuration."""
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = config["tool"]["pytest"]["ini_options"]
    assert {
        "--strict-config",
        "--strict-markers",
        "scripts.ci.pytest_branch_coverage",
        "--cov=markweave",
        "--cov-branch",
        "--cov-report=json:coverage.json",
        "--cov-fail-under=90",
    }.issubset(pytest_options["addopts"])
    assert config["tool"]["coverage"]["run"] == {
        "branch": True,
        "source": ["markweave"],
    }
    assert config["tool"]["coverage"]["report"]["fail_under"] == 90
    assert config["tool"]["ruff"]["target-version"] == "py314"
    assert "TID" in config["tool"]["ruff"]["lint"]["select"]
    assert (
        "unittest.mock"
        in config["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]
    )
    assert config["tool"]["ty"]["environment"]["python-version"] == "3.14"
    assert config["tool"]["ty"]["src"]["include"] == ["src", "scripts", "tests"]


@pytest.mark.unit
def test_changed_line_parser_limits_results_to_application_python() -> None:
    """Coverage changes outside the public application package cannot affect the ratio."""
    diff = """\
diff --git a/src/markweave/service.py b/src/markweave/service.py
--- a/src/markweave/service.py
+++ b/src/markweave/service.py
@@ -1,0 +2,2 @@
+first = 1
+second = 2
diff --git a/tests/test_service.py b/tests/test_service.py
--- a/tests/test_service.py
+++ b/tests/test_service.py
@@ -1,0 +2 @@
+assert True
"""
    assert read_changed_lines(diff, source_root=PurePosixPath("src/markweave")) == {
        "src/markweave/service.py": {2, 3}
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("executed", "expected_percent"),
    [([1] * 9, 90.0), ([1] * 8, 80.0)],
)
def test_changed_coverage_calculates_success_and_failure_boundaries(
    executed: list[int], expected_percent: float
) -> None:
    """Exactly 90 percent passes while the next meaningful boundary fails."""
    executed_lines = list(range(1, len(executed) + 1))
    missing_lines = list(range(len(executed) + 1, 11))
    result = calculate_changed_coverage(
        {"src/markweave/service.py": set(range(1, 11))},
        {
            "src/markweave/service.py": _file_coverage(
                executed=executed_lines, missing=missing_lines
            )
        },
    )
    assert result.percent == expected_percent


@pytest.mark.unit
def test_changed_coverage_rejects_unreported_application_file() -> None:
    """A changed source file cannot evade measurement by disappearing from the report."""
    with pytest.raises(CoverageCheckError, match="absent from coverage"):
        calculate_changed_coverage({"src/markweave/new.py": {1}}, {})


@pytest.mark.unit
def test_changed_coverage_rejects_malformed_line_data() -> None:
    """Invalid Coverage.py line data fails closed instead of lowering the denominator."""
    with pytest.raises(CoverageCheckError, match="positive integer line numbers"):
        malformed = _file_coverage(executed=[1], missing=[])
        malformed["executed_lines"] = ["1"]
        calculate_changed_coverage(
            {"src/markweave/service.py": {1}},
            {"src/markweave/service.py": malformed},
        )


@pytest.mark.unit
def test_changed_coverage_rejects_incomplete_arrays_despite_statement_summary() -> None:
    """A nonzero statement count cannot legitimize absent line arrays."""
    incomplete = _file_coverage(executed=[], missing=[])
    incomplete.pop("excluded_lines")
    incomplete["summary"]["num_statements"] = 1
    with pytest.raises(CoverageCheckError, match="incomplete coverage data"):
        calculate_changed_coverage(
            {"src/markweave/service.py": {1}},
            {"src/markweave/service.py": incomplete},
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("num_statements", 2, "inconsistent coverage summary"),
        ("covered_lines", 0, "inconsistent coverage summary"),
    ],
)
def test_changed_coverage_rejects_inconsistent_summary(
    key: str, value: int, message: str
) -> None:
    """Every line summary count must agree with the complete line arrays."""
    malformed = _file_coverage(executed=[1], missing=[])
    malformed["summary"][key] = value
    with pytest.raises(CoverageCheckError, match=message):
        calculate_changed_coverage(
            {"src/markweave/service.py": {1}},
            {"src/markweave/service.py": malformed},
        )


@pytest.mark.unit
def test_changed_coverage_rejects_overlapping_line_sets() -> None:
    """A line cannot be both executed and missing or excluded."""
    malformed = _file_coverage(executed=[1], missing=[1], excluded=[1])
    with pytest.raises(CoverageCheckError, match="sets overlap"):
        calculate_changed_coverage(
            {"src/markweave/service.py": {1}},
            {"src/markweave/service.py": malformed},
        )


@pytest.mark.unit
def test_changed_coverage_rejects_overlapping_branch_sets() -> None:
    """A branch pair cannot be reported as both executed and missing."""
    malformed = _file_coverage(
        executed=[1],
        missing=[],
        executed_branches=[[1, 2]],
        missing_branches=[[1, 2]],
    )
    with pytest.raises(CoverageCheckError, match="branch coverage sets overlap"):
        calculate_changed_coverage(
            {"src/markweave/service.py": {1}},
            {"src/markweave/service.py": malformed},
        )


@pytest.mark.unit
def test_changed_coverage_preserves_valid_non_executable_and_excluded_lines() -> None:
    """A valid change containing only excluded or non-executable lines remains 0/0."""
    result = calculate_changed_coverage(
        {"src/markweave/service.py": {2, 3}},
        {
            "src/markweave/service.py": _file_coverage(
                executed=[1], missing=[], excluded=[2]
            )
        },
    )
    assert result.executable == 0
    assert result.percent == 100.0


@pytest.mark.unit
def test_coverage_loader_rejects_malformed_document(tmp_path: Path) -> None:
    """Malformed coverage input fails closed with a stable diagnostic."""
    report = tmp_path / "coverage.json"
    report.write_text("[]", encoding="utf-8")
    with pytest.raises(CoverageCheckError, match="contain an object"):
        load_coverage(report)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("covered", "branches", "expected"), [(9, 10, 90.0), (89, 100, 89.0)]
)
def test_branch_only_ratio_has_exact_success_and_failure_boundaries(
    covered: int, branches: int, expected: float
) -> None:
    """Combined line coverage cannot conceal a branch-only ratio below 90%."""
    coverage = check_branch_coverage.calculate_branch_coverage(
        {
            "covered_branches": covered,
            "missing_branches": branches - covered,
            "num_branches": branches,
            "num_partial_branches": 0,
        }
    )
    assert coverage.percent == expected


@pytest.mark.unit
def test_branch_only_ratio_defines_valid_zero_branch_report_as_complete(
    tmp_path: Path,
) -> None:
    """Instrumented code with no branches passes only when all branch totals are zero."""
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(_branch_report(covered=0, branches=0)))
    assert check_branch_coverage.validate_branch_report(report).percent == 100.0
    assert (
        check_branch_coverage.main(["--coverage", str(report), "--fail-under", "90"])
        == 0
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "totals",
    [
        {"covered_branches": 0, "missing_branches": 0, "num_branches": 0},
        {
            "covered_branches": 1,
            "missing_branches": 0,
            "num_branches": 0,
            "num_partial_branches": 0,
        },
        {
            "covered_branches": 0,
            "missing_branches": 0,
            "num_branches": "0",
            "num_partial_branches": 0,
        },
    ],
)
def test_branch_only_ratio_rejects_missing_or_malformed_totals(
    totals: dict[str, object],
) -> None:
    """Zero branches never bypass malformed or internally inconsistent totals."""
    with pytest.raises(CoverageCheckError):
        check_branch_coverage.calculate_branch_coverage(totals)


@pytest.mark.unit
@pytest.mark.parametrize(("covered", "expected"), [(9, 0), (8, 1)])
def test_branch_only_cli_enforces_threshold(
    tmp_path: Path, covered: int, expected: int
) -> None:
    """The JSON entrypoint passes exact 90% and blocks the next lower boundary."""
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(_branch_report(covered=covered, branches=10)))
    assert (
        check_branch_coverage.main(["--coverage", str(report), "--fail-under", "90"])
        == expected
    )


@pytest.mark.unit
@pytest.mark.parametrize(("covered", "fails"), [(9, False), (8, True)])
def test_canonical_pytest_hook_enforces_branch_only_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    covered: int,
    fails: bool,
) -> None:
    """Canonical Pytest exits unsuccessfully below 90% even if its tests passed."""
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(_branch_report(covered=covered, branches=10)))
    monkeypatch.chdir(tmp_path)
    session = mocker.Mock(exitstatus=pytest.ExitCode.OK)
    session.config.getoption.return_value = False
    session.config.pluginmanager.getplugin.return_value = mocker.Mock()
    pytest_branch_coverage.pytest_sessionfinish(session)
    expected = pytest.ExitCode.TESTS_FAILED if fails else pytest.ExitCode.OK
    assert session.exitstatus == expected


@pytest.mark.unit
def test_cli_rejects_invalid_threshold_without_git_access(tmp_path: Path) -> None:
    """Invalid policy values fail before any external process is needed."""
    assert (
        main(
            [
                "--base",
                "base",
                "--head",
                "head",
                "--coverage",
                str(tmp_path / "unused.json"),
                "--fail-under",
                "101",
            ]
        )
        == 2
    )
