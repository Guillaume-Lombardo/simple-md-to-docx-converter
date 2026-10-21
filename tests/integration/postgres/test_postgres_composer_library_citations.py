"""Distributed PostgreSQL/S3 proof of cited library excerpts in model steps."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from markweave.persistence.sql import create_database_engine
from tests.integration.postgres.test_postgres_composer_foundations import _store
from tests.integration.sqlite.test_composer_library_citations_http import (
    exercise_citation_access_is_rechecked,
    exercise_cited_question_resume,
    exercise_cited_step_is_reviewed_and_published,
)
from tests.integration.sqlite.test_composer_office_edit_steps_http import (
    OfficeStepHarness,
    office_step_harness,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


@pytest.fixture
def harness(tmp_path: Path, mocker: MockerFixture) -> Iterator[OfficeStepHarness]:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    objects = _store()
    try:
        with office_step_harness(engine, objects, mocker, tmp_path) as created:
            yield created
    finally:
        objects.close()
        engine.dispose()


def test_postgres_s3_cited_step_is_reviewed_and_published(
    harness: OfficeStepHarness,
) -> None:
    exercise_cited_step_is_reviewed_and_published(harness)


def test_postgres_s3_citation_access_is_rechecked_before_egress(
    harness: OfficeStepHarness,
) -> None:
    exercise_citation_access_is_rechecked(harness)


def test_postgres_s3_cited_question_resume_requires_its_citations(
    harness: OfficeStepHarness,
) -> None:
    exercise_cited_question_resume(harness)
