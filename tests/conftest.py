"""Shared fixtures for the reference corpus."""

import gc
import logging
import os
import sys
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.dependencies import models as fastapi_dependency_models

from markweave.observability import JsonLogFormatter
from markweave.persistence import sql as sql_persistence
from scripts.testing.services import (
    configured_environment,
    distributed_services,
    reap_stale_stacks,
)
from tests.container_runtime_guard import ContainerRuntimeGuard
from tests.golden.corpus import CorpusManifest, materialize_case, read_manifest

DISTRIBUTED_SERVICE_MARKERS = ("requires_postgres", "requires_s3")
DISTRIBUTED_SERVICES_GROUP = "distributed-services"
SESSION_LEAKS = pytest.StashKey[list[str]]()
WORKER_LEAKS_OUTPUT = "markweave_session_resource_leaks"
WORKER_CRASHED = pytest.StashKey[bool]()
APPLICATION_LOGGER = "md_converter.application"
FASTAPI_DEPENDENCY_CACHES = (
    "_is_gen_callable_cached",
    "_is_async_gen_callable_cached",
    "_is_coroutine_callable_cached",
)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Keep service-backed tests on one xdist worker under ``--dist loadgroup``.

    Each worker process owns its own session fixture, so other distribution
    modes stay isolated by starting one private service stack per worker.
    """
    if not config.pluginmanager.hasplugin("xdist"):
        return
    for item in items:
        if any(item.get_closest_marker(name) for name in DISTRIBUTED_SERVICE_MARKERS):
            item.add_marker(pytest.mark.xdist_group(DISTRIBUTED_SERVICES_GROUP))


@pytest.fixture(scope="session")
def local_distributed_services() -> Iterator[None]:
    """Only boundary tests acquire infrastructure; unit-only runs never start it."""
    with distributed_services():
        yield


@pytest.fixture(autouse=True)
def configure_distributed_test_services(request: pytest.FixtureRequest) -> None:
    if any(
        request.node.get_closest_marker(marker) is not None
        for marker in DISTRIBUTED_SERVICE_MARKERS
    ):
        request.getfixturevalue("local_distributed_services")


def pytest_configure(config: pytest.Config) -> None:
    """Fail on resource leaks and on container runtimes launched without markers."""

    if not config.pluginmanager.is_blocked("container-runtime-guard"):
        config.pluginmanager.register(
            ContainerRuntimeGuard(), "container-runtime-guard"
        )
    config.addinivalue_line("filterwarnings", "error::ResourceWarning")
    config.addinivalue_line(
        "filterwarnings",
        "error::pytest.PytestUnraisableExceptionWarning",
    )


@pytest.fixture(autouse=True)
def dispose_every_created_database_engine(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Own every real SQLAlchemy engine at its lowest test acquisition boundary."""

    create_engine = sql_persistence.create_engine

    def create_tracked_engine(url: Any, **kwargs: Any) -> Any:
        engine = create_engine(url, **kwargs)
        request.addfinalizer(engine.dispose)
        return engine

    monkeypatch.setattr(sql_persistence, "create_engine", create_tracked_engine)


def pytest_collection_finish() -> None:
    """Exclude long-lived collection objects from every per-test collection."""
    gc.collect()
    gc.freeze()


@pytest.fixture(autouse=True)
def collect_unreachable_resources() -> Iterator[None]:
    """Attribute finalizer warnings to the test that abandoned the resource.

    The collection runs at this test's teardown, so a resource abandoned in a
    reference cycle fails the test that created it. Freezing the survivors
    afterwards keeps every later collection proportional to the objects one
    test creates; without it, each collection traversed every object retained
    by earlier tests and suite time grew quadratically. Frozen objects that
    become unreachable later, such as module- or session-scoped fixture state,
    are collected by the session-finish backstop instead.

    pytest orders autouse fixtures of one conftest by name, so this fixture is
    set up first and torn down after the other shared function fixtures.
    """
    yield
    clear_fastapi_dependency_caches()
    gc.collect()
    gc.freeze()


def clear_fastapi_dependency_caches() -> None:
    """Drop FastAPI's per-process callable caches, which pin test-built apps.

    Each cache key wraps a bound dependency method, so every application,
    engine and metadata object a test builds stays reachable until the entry
    is evicted. A missing cache is skipped here and fails the guard test in
    ``tests/test_test_harness.py`` instead of every test.
    """
    for name in FASTAPI_DEPENDENCY_CACHES:
        cache = getattr(fastapi_dependency_models, name, None)
        if cache is not None:
            cache.cache_clear()


@pytest.fixture(autouse=True)
def isolate_application_log_handlers() -> Iterator[None]:
    """Remove application log handlers a test installs once the test ends.

    The application logger is configured lazily with a handler bound to the
    current ``sys.stdout``. Inside a test that is pytest's per-test capture
    stream, which is closed afterwards, so a handler that outlived its test
    wrote "I/O operation on closed file" errors into whichever later test
    logged next on the same worker; xdist ordering made that test vary.
    """
    logger = logging.getLogger(APPLICATION_LOGGER)
    before = list(logger.handlers)
    yield
    # pytest attaches and detaches its own capture handlers on non-propagating
    # loggers around every phase; only the application's handlers are ours.
    for handler in logger.handlers[:]:
        if handler not in before and isinstance(handler.formatter, JsonLogFormatter):
            logger.removeHandler(handler)
            handler.close()


def _collect_frozen_garbage() -> list[str]:
    """Unfreeze and collect every object, recording each finalizer failure."""
    failures: list[str] = []

    def record(unraisable: sys.UnraisableHookArgs) -> None:
        message = unraisable.err_msg or "Exception ignored in"
        failures.append(
            f"{message}: {unraisable.object!r}\n"
            + "".join(
                traceback.format_exception(
                    unraisable.exc_type,
                    unraisable.exc_value,
                    unraisable.exc_traceback,
                )
            )
        )

    previous_hook = sys.unraisablehook
    sys.unraisablehook = record
    try:
        gc.unfreeze()
        gc.collect()
    finally:
        sys.unraisablehook = previous_hook
    return failures


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    """Fail the session when frozen garbage abandoned an unclosed resource.

    This runs after session-scoped fixtures are torn down and inside the
    session's warning filters, so ``error::ResourceWarning`` still applies.
    An xdist worker hands its findings to the controller, which reports them.
    """
    config = session.config
    reap_after_worker_crash(config)
    leaks = config.stash.setdefault(SESSION_LEAKS, [])
    leaks.extend(_collect_frozen_garbage())
    worker_output = getattr(config, "workeroutput", None)
    if worker_output is not None:
        worker_output[WORKER_LEAKS_OUTPUT] = list(leaks)
    if leaks and session.exitstatus in {
        pytest.ExitCode.OK,
        pytest.ExitCode.NO_TESTS_COLLECTED,
    }:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def reap_after_worker_crash(config: pytest.Config) -> tuple[str, ...]:
    """Remove the service stack a crashed xdist worker could not tear down.

    A crashed worker releases its stack lock with its process, so the
    controller can prove the stack abandoned without waiting for the next
    session. Stacks of live workers and sessions keep their locks and stay.
    """
    if not config.stash.get(WORKER_CRASHED, False) or configured_environment(
        os.environ
    ):
        return ()
    try:
        return reap_stale_stacks()
    except RuntimeError:
        return ()


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    """Collect a finished xdist worker's leaks and remember a crashed worker."""
    if error is not None:
        node.config.stash[WORKER_CRASHED] = True
    worker_output = getattr(node, "workeroutput", None) or {}
    node.config.stash.setdefault(SESSION_LEAKS, []).extend(
        worker_output.get(WORKER_LEAKS_OUTPUT, ())
    )


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    leaks = terminalreporter.config.stash.get(SESSION_LEAKS, [])
    if not leaks:
        return
    terminalreporter.write_sep(
        "=", "resources abandoned outside a single test", red=True
    )
    for leak in leaks:
        terminalreporter.write_line(leak)


@pytest.fixture(scope="session")
def corpus_manifest() -> CorpusManifest:
    return read_manifest(Path("tests/corpus/manifest.json"))


@pytest.fixture
def materialize_corpus_case(
    corpus_manifest: CorpusManifest, tmp_path: Path
) -> Callable[[str], Path]:
    def materialize(case_id: str) -> Path:
        return materialize_case(
            corpus_manifest, corpus_manifest.by_id(case_id), tmp_path
        )

    return materialize
