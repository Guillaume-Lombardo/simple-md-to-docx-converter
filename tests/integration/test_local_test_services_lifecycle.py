"""Real disposable-service cleanup after failures, interrupts and crashes."""

import os
import signal
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.testing import services

pytest_plugins = ["pytester"]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
    pytest.mark.requires_docker,
]


def test_failed_test_body_removes_only_its_owned_service_resources(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    # The session fixture's configured stack stays alive; this nested stack must
    # clean only its own project and leave the outer test environment intact.
    for variable in services.VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    compose = mocker.spy(services, "_compose")
    with (
        pytest.raises(RuntimeError, match="deliberate test failure"),
        services.distributed_services(),
    ):
        raise RuntimeError("deliberate test failure")
    # The reaper may first remove another abandoned stack; find this one.
    (project,) = {
        call.args[0] for call in compose.call_args_list if call.args[2] == "up"
    }
    assert _resources(project) == {}


def _resources(project: str) -> dict[str, str]:
    """Return the non-empty container, volume and network listings of a project."""
    found: dict[str, str] = {}
    for resource in ("container", "volume", "network"):
        result = subprocess.run(
            [
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                resource,
                "ls",
                "--quiet",
                *(["--all"] if resource == "container" else []),
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.stdout.strip():
            found[resource] = result.stdout.strip()
    return found


ROOT = Path(__file__).resolve().parents[2]
OWNER = """\
import time
from scripts.testing import services

with services.distributed_services():
    print(next(iter(services._OWNED)), flush=True)
    time.sleep(600)
"""


def _child_environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in services.VARIABLES
    }
    environment["PYTHONPATH"] = str(ROOT)
    return environment


@contextmanager
def _owner() -> Generator[tuple[subprocess.Popen[str], str]]:
    """Run a separate session that owns one real stack until it is signalled."""
    process = subprocess.Popen(
        [sys.executable, "-c", OWNER],
        cwd=ROOT,
        env=_child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        project = process.stdout.readline().strip()
        assert services.PROJECT_PATTERN.fullmatch(project), process.communicate()
        yield process, project
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=120)


@pytest.mark.slow
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["term", "int"])
def test_signalled_owner_removes_its_stack_and_lock(signum: signal.Signals) -> None:
    with _owner() as (process, project):
        assert _resources(project)
        process.send_signal(signum)
        process.communicate(timeout=120)
    assert process.returncode != 0
    assert _resources(project) == {}
    assert not (services.lock_directory() / f"{project}.lock").exists()


@pytest.mark.slow
def test_reaper_removes_a_killed_owner_and_keeps_a_live_one() -> None:
    with _owner() as (live, kept), _owner() as (killed, reaped):
        killed.kill()
        killed.communicate(timeout=60)
        assert _resources(reaped)
        assert reaped in services.reap_stale_stacks()
        assert _resources(reaped) == {}
        assert not (services.lock_directory() / f"{reaped}.lock").exists()
        assert kept not in services.reap_stale_stacks()
        assert _resources(kept)
        assert live.poll() is None
    assert _resources(kept) == {}


CRASHING_CONFTEST = """\
from tests.conftest import (
    configure_distributed_test_services,
    local_distributed_services,
    pytest_collection_modifyitems,
    pytest_sessionfinish,
    pytest_testnodedown,
)
"""
CRASHING_TEST = """\
import os
import signal
from pathlib import Path

import pytest

from scripts.testing import services


@pytest.mark.requires_postgres
def test_worker_crashes_while_owning_services():
    Path(os.environ["CRASHED_PROJECT_FILE"]).write_text(next(iter(services._OWNED)))
    os.kill(os.getpid(), signal.SIGKILL)
"""


@pytest.mark.slow
def test_controller_removes_the_stack_of_a_crashed_xdist_worker(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in _child_environment().items():
        monkeypatch.setenv(name, value)
    for name in services.VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    record = pytester.path / "project.txt"
    monkeypatch.setenv("CRASHED_PROJECT_FILE", str(record))
    pytester.makeini(
        "[pytest]\nmarkers =\n    requires_postgres: requires PostgreSQL\n"
    )
    pytester.makeconftest(CRASHING_CONFTEST)
    pytester.makepyfile(test_crash=CRASHING_TEST)
    result = pytester.runpytest_subprocess(
        "-p",
        "no:cacheprovider",
        "-p",
        "no:pytest_cov",
        "-n",
        "1",
        "--max-worker-restart",
        "0",
    )
    assert result.ret != pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*crashed*"])
    project = record.read_text()
    assert services.PROJECT_PATTERN.fullmatch(project)
    assert _resources(project) == {}
