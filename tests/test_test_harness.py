"""The shared test harness keeps per-test resource attribution after freezing."""

from __future__ import annotations

import gc
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_collection_objects_are_frozen_before_tests_run() -> None:
    """Per-test collections skip the long-lived objects built during collection."""
    assert gc.get_freeze_count() > 0


@pytest.mark.integration
@pytest.mark.light_coverage
def test_resource_abandoned_in_a_cycle_still_fails_its_own_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cyclic leak created by a test is collected and reported at its teardown."""
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    pytester.makeini("[pytest]\n")
    pytester.makeconftest(
        "from tests.conftest import (\n"
        "    collect_unreachable_resources,\n"
        "    pytest_collection_finish,\n"
        "    pytest_configure,\n"
        ")\n"
    )
    pytester.makepyfile(
        test_sample=(
            "import gc\n"
            "import warnings\n\n\n"
            "class Resource:\n"
            "    def __del__(self):\n"
            '        warnings.warn("abandoned resource", ResourceWarning)\n\n\n'
            "def test_leaks_cycle():\n"
            "    assert gc.get_freeze_count() > 0\n"
            "    resource = Resource()\n"
            "    resource.self = resource\n\n\n"
            "def test_clean():\n"
            "    pass\n"
        )
    )
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:pytest_cov", "-p", "no:xdist"
    )
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(
        ["*ERROR at teardown of test_leaks_cycle*", "*abandoned resource*"]
    )
