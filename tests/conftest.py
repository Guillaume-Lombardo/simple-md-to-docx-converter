"""Shared fixtures for the reference corpus."""

import gc
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.golden.corpus import CorpusManifest, materialize_case, read_manifest


def pytest_configure(config: pytest.Config) -> None:
    """Fail on application resource leaks without suppressing other warnings."""

    config.addinivalue_line("filterwarnings", "error::ResourceWarning")
    config.addinivalue_line(
        "filterwarnings",
        "error::pytest.PytestUnraisableExceptionWarning",
    )


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
