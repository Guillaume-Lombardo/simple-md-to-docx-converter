"""The shared test harness keeps resource-leak detection while freezing the heap."""

from __future__ import annotations

import gc
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.dependencies import models
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from tests import conftest

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parents[1]
HARNESS_CONFTEST = """\
from tests.conftest import (
    collect_unreachable_resources,
    pytest_collection_finish,
    pytest_configure,
    pytest_sessionfinish,
    pytest_terminal_summary,
    pytest_testnodedown,
)
"""
RESOURCE = """\
import warnings


class Resource:
    def __init__(self, name):
        self.name = name
        self.cycle = self

    def close(self):
        self.cycle = None

    def __del__(self):
        if self.cycle is not None:
            warnings.warn(f"abandoned {self.name}", ResourceWarning)
"""
SESSION_SECTION = "resources abandoned outside a single test"
# ``loadscope`` keeps each module on one worker, so neighbouring tests share a
# worker heap exactly as they do in a serial run.
MODES = pytest.mark.parametrize(
    "mode",
    [("-p", "no:xdist"), ("-n", "2", "--dist", "loadscope")],
    ids=["serial", "xdist"],
)


def _project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(leaky_resource=RESOURCE)


def _run(pytester: pytest.Pytester, mode: tuple[str, ...]) -> pytest.RunResult:
    return pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "no:pytest_cov", "-rE", *mode
    )


@pytest.mark.unit
def test_objects_are_frozen_before_each_test_runs() -> None:
    """Per-test collections skip every object that survived an earlier test."""
    assert gc.get_freeze_count() > 0


@pytest.mark.integration
@pytest.mark.light_coverage
@MODES
def test_resource_abandoned_in_a_cycle_still_fails_its_own_test(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    mode: tuple[str, ...],
) -> None:
    """A cyclic leak is reported at the teardown of the test that created it.

    Clean tests run before and after the leaking test on the same heap, which
    has already been frozen, and the leak must not move to a neighbour or to
    the session backstop.
    """
    _project(pytester, monkeypatch)
    pytester.makeconftest(HARNESS_CONFTEST)
    pytester.makepyfile(
        test_sample=(
            "import gc\n"
            "from leaky_resource import Resource\n\n\n"
            "def test_clean_before():\n"
            "    assert gc.get_freeze_count() > 0\n\n\n"
            "def test_leaks_cycle():\n"
            "    assert gc.get_freeze_count() > 0\n"
            "    Resource('test resource')\n\n\n"
            "def test_clean_after():\n"
            "    Resource('closed resource').close()\n"
        )
    )
    result = _run(pytester, mode)
    result.assert_outcomes(passed=3, errors=1)
    result.stdout.fnmatch_lines(
        ["*ERROR*test_leaks_cycle*", "*abandoned test resource*"]
    )
    output = result.stdout.str()
    errors = [line for line in output.splitlines() if "ERROR " in line]
    assert errors
    assert all("test_leaks_cycle" in line for line in errors), errors
    assert SESSION_SECTION not in output


@pytest.mark.integration
@pytest.mark.light_coverage
@MODES
@pytest.mark.parametrize("scope", ["module", "session"])
def test_long_lived_leak_is_caught_by_the_session_backstop(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    mode: tuple[str, ...],
    scope: str,
) -> None:
    """A fixture frozen while alive and abandoned at teardown fails the session."""
    _project(pytester, monkeypatch)
    pytester.makeconftest(
        HARNESS_CONFTEST
        + "import pytest\n"
        + "from leaky_resource import Resource\n\n\n"
        + f"@pytest.fixture(scope='{scope}')\n"
        + "def shared():\n"
        + f"    yield Resource('{scope} resource')\n"
    )
    pytester.makepyfile(
        test_first="def test_one(shared):\n    pass\n\n\ndef test_two(shared):\n"
        "    pass\n",
        test_second="def test_three():\n    pass\n\n\ndef test_four():\n    pass\n",
    )
    result = _run(pytester, mode)
    result.assert_outcomes(passed=4)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        [f"*{SESSION_SECTION}*", f"*abandoned {scope} resource*"]
    )


@pytest.mark.integration
@pytest.mark.light_coverage
@MODES
def test_closed_long_lived_resource_passes_the_session_backstop(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    mode: tuple[str, ...],
) -> None:
    """The backstop reports nothing when long-lived fixtures release resources."""
    _project(pytester, monkeypatch)
    pytester.makeconftest(
        HARNESS_CONFTEST
        + "import pytest\n"
        + "from leaky_resource import Resource\n\n\n"
        + "@pytest.fixture(scope='session')\n"
        + "def shared():\n"
        + "    resource = Resource('session resource')\n"
        + "    yield resource\n"
        + "    resource.close()\n"
    )
    pytester.makepyfile(
        test_sample="def test_one(shared):\n    pass\n\n\ndef test_two(shared):\n"
        "    pass\n"
    )
    result = _run(pytester, mode)
    result.assert_outcomes(passed=2)
    assert result.ret == pytest.ExitCode.OK
    assert SESSION_SECTION not in result.stdout.str()


REAPED = ("markweave-tests-reaped",)
CRASH_SCENARIOS = {
    "no-crash": (False, False, None, ()),
    "external-services": (True, True, None, ()),
    "crash": (True, False, None, REAPED),
    "docker-unavailable": (True, False, RuntimeError("docker"), ()),
}


@pytest.mark.unit
@pytest.mark.parametrize("scenario", CRASH_SCENARIOS)
def test_controller_reaps_service_stacks_after_a_worker_crash(
    mocker: MockerFixture, scenario: str
) -> None:
    """A crashed worker's stack is removed before the session ends."""
    crashed, configured, failure, expected = CRASH_SCENARIOS[scenario]
    config = mocker.Mock(stash=pytest.Stash())
    node = mocker.Mock(config=config, workeroutput={})
    conftest.pytest_testnodedown(node, "worker crashed" if crashed else None)
    mocker.patch.object(conftest, "configured_environment", return_value=configured)
    reap = mocker.patch.object(
        conftest, "reap_stale_stacks", return_value=REAPED, side_effect=failure
    )
    assert conftest.reap_after_worker_crash(config) == expected
    assert reap.called is (crashed and not configured)


@pytest.mark.unit
def test_fastapi_dependency_caches_still_exist_and_are_cleared() -> None:
    """Fail loudly when a FastAPI upgrade renames the caches the harness clears."""
    found = [getattr(models, name, None) for name in conftest.FASTAPI_DEPENDENCY_CACHES]
    caches = [cache for cache in found if hasattr(cache, "cache_info")]
    assert len(caches) == len(found), (
        "FastAPI renamed its dependency caches; update FASTAPI_DEPENDENCY_CACHES "
        "in tests/conftest.py or drop the clearing."
    )

    class Dependency:
        def __call__(self) -> int:
            return 1

    application = FastAPI()

    @application.get("/")
    def read(value: int = Depends(Dependency())) -> int:
        return value

    with TestClient(application) as client:
        assert client.get("/").json() == 1
    assert sum(cache.cache_info().currsize for cache in caches) > 0
    conftest.clear_fastapi_dependency_caches()
    assert sum(cache.cache_info().currsize for cache in caches) == 0
