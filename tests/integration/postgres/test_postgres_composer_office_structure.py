"""Distributed PostgreSQL/S3 proof of structural DOCX and PPTX edits."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from markweave.persistence.sql import create_database_engine
from tests.integration.postgres.test_postgres_composer_foundations import _store
from tests.integration.sqlite.test_composer_docx_edits_http import (
    DocxEditHarness,
    docx_edit_harness,
)
from tests.integration.sqlite.test_composer_office_structure_http import (
    exercise_docx_structure,
    exercise_image_rejections,
    exercise_pptx_slide_order,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


@pytest.fixture
def harness(tmp_path: Path, mocker: MockerFixture) -> Iterator[DocxEditHarness]:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    objects = _store()
    try:
        with docx_edit_harness(engine, objects, mocker, tmp_path) as created:
            yield created
    finally:
        objects.close()
        engine.dispose()


def test_postgres_s3_docx_styles_and_images_publish_exact_revisions(
    harness: DocxEditHarness,
) -> None:
    exercise_docx_structure(harness)


def test_postgres_s3_pptx_slides_are_reordered_exactly(
    harness: DocxEditHarness,
) -> None:
    exercise_pptx_slide_order(harness)


def test_postgres_s3_rejected_images_never_publish(
    harness: DocxEditHarness, mocker: MockerFixture
) -> None:
    exercise_image_rejections(harness, mocker)
