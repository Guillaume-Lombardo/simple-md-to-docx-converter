"""Shared fixtures for the reference corpus."""

import gc
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from markweave.persistence import sql as sql_persistence
from tests.golden.corpus import CorpusManifest, materialize_case, read_manifest


def pytest_configure(config: pytest.Config) -> None:
    """Fail on application resource leaks without suppressing other warnings."""

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
