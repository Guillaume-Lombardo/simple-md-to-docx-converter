"""Distributed PostgreSQL/S3 proof of the permissioned, cited library."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from markweave.persistence.sql import create_database_engine
from tests.integration.postgres.test_postgres_composer_foundations import _store
from tests.integration.sqlite.test_composer_library_http import (
    LibraryHarness,
    exercise_concurrent_replacements,
    exercise_durable_deletion,
    exercise_library_lifecycle,
    exercise_office_and_rejected_sources,
    exercise_publication_failures_and_recovery,
    exercise_request_validation,
    exercise_unicode_search,
    library_harness,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


@pytest.fixture
def harness(tmp_path: Path, mocker: MockerFixture) -> Iterator[LibraryHarness]:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    objects = _store()
    try:
        with library_harness(engine, objects, mocker, tmp_path) as created:
            yield created
    finally:
        objects.close()
        engine.dispose()


def test_postgres_s3_library_filters_access_before_text_is_returned(
    harness: LibraryHarness,
) -> None:
    exercise_library_lifecycle(harness)


def test_postgres_s3_office_sources_and_rejections(harness: LibraryHarness) -> None:
    exercise_office_and_rejected_sources(harness)


def test_postgres_s3_concurrent_replacements_publish_one_version(
    harness: LibraryHarness,
) -> None:
    exercise_concurrent_replacements(harness)


def test_postgres_s3_failed_publications_and_recovery(
    harness: LibraryHarness, mocker: MockerFixture
) -> None:
    exercise_publication_failures_and_recovery(harness, mocker)


def test_postgres_s3_requests_are_validated_before_storage(
    harness: LibraryHarness,
) -> None:
    exercise_request_validation(harness)


def test_postgres_s3_deleted_sources_are_eventually_removed(
    harness: LibraryHarness, mocker: MockerFixture
) -> None:
    exercise_durable_deletion(harness, mocker)


def test_postgres_s3_unicode_search_matches_folded_text(
    harness: LibraryHarness,
) -> None:
    exercise_unicode_search(harness)
