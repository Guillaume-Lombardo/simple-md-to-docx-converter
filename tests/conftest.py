"""Shared fixtures for the reference corpus."""

import gc
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from markweave.persistence import sql as sql_persistence
from scripts.testing.services import distributed_services
from tests.container_runtime_guard import ContainerRuntimeGuard
from tests.golden.corpus import CorpusManifest, materialize_case, read_manifest

DISTRIBUTED_SERVICE_MARKERS = ("requires_postgres", "requires_s3")
DISTRIBUTED_SERVICES_GROUP = "distributed-services"


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


@pytest.fixture(autouse=True)
def collect_unreachable_resources() -> Iterator[None]:
    """Attribute finalizer warnings to the test that abandoned the resource."""

    yield
    gc.collect()


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
