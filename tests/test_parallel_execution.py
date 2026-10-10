"""Parallel pytest-xdist runs keep coverage gates and the runtime guard equivalent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parents[1]
APPLICATION = """\
def first(value):
    if value:
        return 1
    return 0


def second(value):
    if value:
        return 1
    return 0


def third(value):
    if value:
        return 1
    return 0


def fourth(value):
    if value:
        return 1
    return 0


def fifth(value):
    if value:
        return 1
    return 0
"""
# Each test exercises one function branch, so a two-worker run must merge both
# workers' data to reach the serial totals.
CASES = (
    ("first", True),
    ("first", False),
    ("second", True),
    ("second", False),
    ("third", True),
    ("third", False),
    ("fourth", True),
    ("fourth", False),
    ("fifth", True),
)


def _write_project(directory: Path, cases: tuple[tuple[str, bool], ...]) -> None:
    (directory / "application.py").write_text(APPLICATION)
    (directory / "test_application.py").write_text(
        "import application\n"
        + "".join(
            f"def test_{index}():\n    application.{name}({value})\n"
            for index, (name, value) in enumerate(cases)
        )
    )


def _run(directory: Path, *options: str) -> subprocess.CompletedProcess[str]:
    environment: dict[str, str] = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": os.pathsep.join((str(ROOT), str(directory))),
        "COVERAGE_FILE": str(directory / ".coverage"),
    }
    environment.pop("PYTEST_XDIST_WORKER", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            "--rootdir",
            str(directory),
            "-q",
            "-p",
            "xdist",
            "-p",
            "pytest_cov.plugin",
            "-p",
            "scripts.ci.pytest_branch_coverage",
            "--cov=application",
            "--cov-branch",
            "--cov-report=json:coverage.json",
            "--cov-fail-under=0",
            *options,
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _measurements(directory: Path) -> dict[str, object]:
    report = json.loads((directory / "coverage.json").read_text())
    (data,) = report["files"].values()
    return {
        "totals": report["totals"],
        "executed_lines": data["executed_lines"],
        "missing_lines": data["missing_lines"],
        "executed_branches": data["executed_branches"],
        "missing_branches": data["missing_branches"],
    }


@pytest.mark.integration
@pytest.mark.light_coverage
@pytest.mark.parametrize(
    ("cases", "expected_returncode", "expected_percent"),
    [(CASES, 0, 90.0), (CASES[:-1], 1, 80.0)],
)
def test_xdist_merges_worker_coverage_and_keeps_branch_gate(
    tmp_path: Path,
    cases: tuple[tuple[str, bool], ...],
    expected_returncode: int,
    expected_percent: float,
) -> None:
    """Serial and two-worker runs report identical data and gate results."""
    measurements = []
    for name, options in (("serial", ()), ("parallel", ("-n", "2"))):
        directory = tmp_path / name
        directory.mkdir()
        _write_project(directory, cases)
        result = _run(directory, *options)
        output = result.stdout + result.stderr
        assert result.returncode == expected_returncode, output
        assert f"{len(cases)} passed" in output
        failure = "branch-only coverage failure"
        assert output.count(failure) == (1 if expected_returncode else 0), output
        measurements.append(_measurements(directory))
        assert not list(directory.glob(".coverage.*")), "worker data left unmerged"
    serial, parallel = measurements
    assert parallel == serial
    totals = serial["totals"]
    assert isinstance(totals, dict)
    assert totals["num_branches"] == 10
    assert totals["covered_branches"] * 100 / totals["num_branches"] == (
        expected_percent
    )


@pytest.mark.integration
@pytest.mark.light_coverage
def test_xdist_worker_failure_is_reported_once_by_the_controller(
    tmp_path: Path,
) -> None:
    """A malformed report fails the controller session, never a worker silently."""
    _write_project(tmp_path, CASES)
    result = _run(tmp_path, "-n", "2", "--cov-report=json:other.json")
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert output.count("branch-only coverage validation failed") == 1, output


GUARD_CONFTEST = """\
from tests.container_runtime_guard import ContainerRuntimeGuard


def pytest_configure(config):
    config.pluginmanager.register(ContainerRuntimeGuard(), "container-runtime-guard")
"""
MISSING = "/nonexistent/bin/podman"


@pytest.mark.integration
@pytest.mark.light_coverage
def test_runtime_guard_enforces_markers_in_every_xdist_worker(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each worker installs the audit hook, including the forked-child path."""
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    pytester.makeini("[pytest]\nmarkers =\n    requires_podman: requires Podman\n")
    pytester.makeconftest(GUARD_CONFTEST)
    pytester.makepyfile(
        test_sample=(
            "import os, subprocess, pytest\n\n"
            + "".join(
                f"def test_unmarked_{index}():\n"
                f'    subprocess.run(["{MISSING}", "ps"])\n\n'
                for index in range(4)
            )
            + "def test_unmarked_spawn():\n"
            f'    os.spawnv(os.P_WAIT, "/bin/echo", ["echo", "{MISSING}"])\n\n'
            + "".join(
                "@pytest.mark.requires_podman\n"
                f"def test_marked_{index}():\n"
                "    with pytest.raises(FileNotFoundError):\n"
                f'        subprocess.run(["{MISSING}", "ps"])\n\n'
                for index in range(4)
            )
        )
    )
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:pytest_cov", "-n", "2", "-rf"
    )
    result.assert_outcomes(passed=4, failed=5)
    result.stdout.fnmatch_lines(["*2 workers*"])
    output = result.stdout.str()
    assert output.count("launched podman without pytest.mark.requires_podman") >= 5
    for index in range(4):
        assert f"FAILED test_sample.py::test_unmarked_{index}" in output
    assert "FAILED test_sample.py::test_unmarked_spawn" in output
