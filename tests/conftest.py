"""Shared fixtures for the reference corpus."""

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.golden.corpus import CorpusManifest, materialize_case, read_manifest


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
