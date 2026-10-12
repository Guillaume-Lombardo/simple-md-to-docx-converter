"""Pytest hook enforcing branch-only coverage after pytest-cov writes JSON."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.check_branch_coverage import validate_branch_report
from scripts.ci.check_changed_coverage import CoverageCheckError

BRANCH_FAIL_UNDER = 90.0


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    """Fail canonical Pytest runs when application branch coverage is below 90%.

    Under pytest-xdist, only the controller validates the report. pytest-cov
    combines every worker's data and writes ``coverage.json`` on the controller
    after all workers finish, so a worker would read a missing or stale file.
    """
    config = session.config
    if (
        config.getoption("no_cov")
        or config.getoption("collectonly")
        or hasattr(config, "workerinput")
    ):
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
