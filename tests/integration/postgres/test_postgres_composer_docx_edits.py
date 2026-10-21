"""Distributed PostgreSQL/S3 proof of the DOCX and PPTX text-edit HTTP workflows."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from markweave.persistence.sql import create_database_engine
from tests.integration.postgres.test_postgres_composer_foundations import _store
from tests.integration.sqlite.test_composer_docx_edits_http import (
    REJECTIONS,
    DocxEditHarness,
    docx_edit_harness,
    exercise_concurrent_edits,
    exercise_edit_workflow,
    exercise_markdown_refusal,
    exercise_nested_markup_refusal,
    exercise_owner_fence,
    exercise_pptx_workflow,
    exercise_rejection,
    exercise_request_validation,
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


def test_postgres_s3_text_edit_publishes_and_restores_exact_bytes(
    harness: DocxEditHarness,
) -> None:
    exercise_edit_workflow(harness)


@pytest.mark.parametrize(("operation", "status", "code", "paths"), REJECTIONS)
def test_postgres_s3_rejected_edits_publish_nothing(
    harness: DocxEditHarness,
    operation: dict[str, str],
    status: int,
    code: str,
    paths: list[str],
) -> None:
    exercise_rejection(harness, operation, status, code, paths)


def test_postgres_s3_requests_are_validated_before_document_work(
    harness: DocxEditHarness,
) -> None:
    exercise_request_validation(harness)


def test_postgres_s3_markdown_revisions_are_refused(
    harness: DocxEditHarness,
) -> None:
    exercise_markdown_refusal(harness)


def test_postgres_s3_other_users_are_fenced(harness: DocxEditHarness) -> None:
    exercise_owner_fence(harness)


def test_postgres_s3_concurrent_edits_publish_one_revision(
    harness: DocxEditHarness,
) -> None:
    exercise_concurrent_edits(harness)


def test_postgres_s3_nested_markup_is_refused_without_publication(
    harness: DocxEditHarness,
) -> None:
    exercise_nested_markup_refusal(harness)


def test_postgres_s3_pptx_slide_and_notes_edits(harness: DocxEditHarness) -> None:
    exercise_pptx_workflow(harness)
