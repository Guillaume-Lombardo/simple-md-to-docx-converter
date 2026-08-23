"""Pytest hook enforcing branch-only coverage after pytest-cov writes JSON."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.check_branch_coverage import validate_branch_report
from scripts.ci.check_changed_coverage import CoverageCheckError

BRANCH_FAIL_UNDER = 90.0


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    """Fail canonical Pytest runs when application branch coverage is below 90%."""
    if session.config.getoption("no_cov") or session.config.getoption("collectonly"):
        return
    try:
        coverage = validate_branch_report(Path("coverage.json"))
    except CoverageCheckError as error:
        message = f"branch-only coverage validation failed: {error}"
    else:
        if coverage.percent >= BRANCH_FAIL_UNDER:
            return
        message = (
            "branch-only coverage failure: "
            f"{coverage.percent:.2f}% is less than fail-under=90"
        )
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    reporter.write(f"\nERROR: {message}\n", red=True, bold=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
