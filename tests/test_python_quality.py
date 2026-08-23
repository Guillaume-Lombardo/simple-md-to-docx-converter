"""Tests for the committed Python quality policy."""

from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath

import pytest

from scripts.ci.check_changed_coverage import (
    CoverageCheckError,
    calculate_changed_coverage,
    load_coverage,
    main,
    read_changed_lines,
)


@pytest.mark.unit
def test_committed_quality_configuration_enforces_required_tools() -> None:
    """The checked-in policy fixes every T05 quality invariant in one configuration."""
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = config["tool"]["pytest"]["ini_options"]
    assert {
        "--strict-config",
        "--strict-markers",
        "--cov=md_converter",
        "--cov-branch",
        "--cov-fail-under=90",
    }.issubset(pytest_options["addopts"])
    assert config["tool"]["coverage"]["run"] == {
        "branch": True,
        "source": ["md_converter"],
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
diff --git a/src/md_converter/service.py b/src/md_converter/service.py
--- a/src/md_converter/service.py
+++ b/src/md_converter/service.py
@@ -1,0 +2,2 @@
+first = 1
+second = 2
diff --git a/tests/test_service.py b/tests/test_service.py
--- a/tests/test_service.py
+++ b/tests/test_service.py
@@ -1,0 +2 @@
+assert True
"""
    assert read_changed_lines(diff, source_root=PurePosixPath("src/md_converter")) == {
        "src/md_converter/service.py": {2, 3}
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
        {"src/md_converter/service.py": set(range(1, 11))},
        {
            "src/md_converter/service.py": {
                "executed_lines": executed_lines,
                "missing_lines": missing_lines,
            }
        },
    )
    assert result.percent == expected_percent


@pytest.mark.unit
def test_changed_coverage_rejects_unreported_application_file() -> None:
    """A changed source file cannot evade measurement by disappearing from the report."""
    with pytest.raises(CoverageCheckError, match="absent from coverage"):
        calculate_changed_coverage({"src/md_converter/new.py": {1}}, {})


@pytest.mark.unit
def test_changed_coverage_rejects_malformed_line_data() -> None:
    """Invalid Coverage.py line data fails closed instead of lowering the denominator."""
    with pytest.raises(CoverageCheckError, match="invalid line coverage data"):
        calculate_changed_coverage(
            {"src/md_converter/service.py": {1}},
            {
                "src/md_converter/service.py": {
                    "executed_lines": ["1"],
                    "missing_lines": [],
                }
            },
        )


@pytest.mark.unit
def test_coverage_loader_rejects_malformed_document(tmp_path: Path) -> None:
    """Malformed coverage input fails closed with a stable diagnostic."""
    report = tmp_path / "coverage.json"
    report.write_text("[]", encoding="utf-8")
    with pytest.raises(CoverageCheckError, match="files object"):
        load_coverage(report)


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
