"""Distributed PostgreSQL/S3 proof of model-proposed Office text edits."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from markweave.persistence.sql import create_database_engine
from tests.integration.postgres.test_postgres_composer_foundations import _store
from tests.integration.sqlite.test_composer_office_edit_steps_http import (
    TOOL_MISUSE,
    OfficeStepHarness,
    exercise_markdown_step_is_not_an_office_edit,
    exercise_model_proposed_edit,
    exercise_pptx_proposal,
    exercise_request_edges,
    exercise_stale_and_invalid_requests,
    exercise_superseded_proposal,
    exercise_tool_misuse,
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


def test_postgres_s3_model_proposed_edit_is_reviewed_and_published(
    harness: OfficeStepHarness,
) -> None:
    exercise_model_proposed_edit(harness)


@pytest.mark.parametrize("reply", TOOL_MISUSE[:4])
def test_postgres_s3_tool_misuse_fails_closed(
    harness: OfficeStepHarness, reply: str
) -> None:
    exercise_tool_misuse(harness, reply)


def test_postgres_s3_stale_and_invalid_requests_never_transmit(
    harness: OfficeStepHarness,
) -> None:
    exercise_stale_and_invalid_requests(harness)


def test_postgres_s3_superseded_proposals_cannot_be_published(
    harness: OfficeStepHarness,
) -> None:
    exercise_superseded_proposal(harness)


def test_postgres_s3_pptx_notes_proposals_publish_exact_bytes(
    harness: OfficeStepHarness,
) -> None:
    exercise_pptx_proposal(harness)


def test_postgres_s3_markdown_steps_cannot_masquerade(
    harness: OfficeStepHarness,
) -> None:
    exercise_markdown_step_is_not_an_office_edit(harness)


def test_postgres_s3_request_edges_fail_closed(harness: OfficeStepHarness) -> None:
    exercise_request_edges(harness)
