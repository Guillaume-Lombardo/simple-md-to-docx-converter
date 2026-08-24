"""Unit coverage for owner-bound idempotent job service behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from md_converter.jobs.errors import JobConflictError, JobNotFoundError
from md_converter.jobs.models import JobOutput, JobRequest, JobState, JobStep
from md_converter.jobs.ports import JobRepository
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.storage import ObjectNotFoundError, ObjectStore
from tests.unit.jobs.test_job_models import job

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 24, tzinfo=UTC)
RETENTION_SECONDS = 100.0
COMPONENT_VERSIONS = (("md-converter", "0.1.0"),)


def service(repository: JobRepository, objects: ObjectStore) -> JobService:
    return JobService(
        repository,
        objects,
        JobServicePolicy(result_retention_seconds=RETENTION_SECONDS),
    )


def test_submission_persists_source_and_reuses_matching_idempotency(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    objects = mocker.Mock(spec=ObjectStore)
    created = job(idempotency_digest="2" * 64)
    repository.create.return_value = (created, False)
    repository.activate_source.return_value = created
    instance = service(repository, objects)
    result, replayed = instance.submit(
        JobRequest(
            owner_id=created.owner_id,
            source=b"source",
            template_id=created.template_id,
            template_version_id=created.template_version_id,
            output=created.output,
            component_versions=COMPONENT_VERSIONS,
            now=NOW,
        ),
        "request-1",
    )
    assert result is created
    assert not replayed
    objects.put.assert_called_once()

    replay = job(
        owner_id=created.owner_id,
        template_id=created.template_id,
        template_version_id=created.template_version_id,
        output=created.output,
        request_digest=repository.create.call_args.args[0].request_digest,
    )
    repository.create.return_value = (replay, True)
    result, replayed = instance.submit(
        JobRequest(
            owner_id=created.owner_id,
            source=b"source",
            template_id=created.template_id,
            template_version_id=created.template_version_id,
            output=JobOutput.PDF,
            component_versions=COMPONENT_VERSIONS,
            now=NOW,
        ),
        "request-1",
    )
    assert result is replay
    assert replayed
    assert objects.put.call_count == 1


def test_submission_rejects_conflict_before_writing_source(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    objects = mocker.Mock(spec=ObjectStore)
    repository.create.return_value = (job(request_digest="f" * 64), True)
    with pytest.raises(JobConflictError):
        service(repository, objects).submit(
            JobRequest(
                owner_id=uuid4(),
                source=b"private",
                template_id=uuid4(),
                template_version_id=uuid4(),
                output=JobOutput.DOCX,
                component_versions=COMPONENT_VERSIONS,
                now=NOW,
            ),
            "same-key",
        )
    objects.put.assert_not_called()


def test_failed_source_upload_leaves_a_recoverable_durable_reservation(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    objects = mocker.Mock(spec=ObjectStore)
    reserved = job(source_ready=False)
    repository.create.return_value = (reserved, False)
    objects.put.side_effect = RuntimeError("object storage unavailable")
    with pytest.raises(RuntimeError, match="object storage unavailable"):
        service(repository, objects).submit(
            JobRequest(
                owner_id=reserved.owner_id,
                source=b"private",
                template_id=reserved.template_id,
                template_version_id=reserved.template_version_id,
                output=reserved.output,
                component_versions=COMPONENT_VERSIONS,
                now=NOW,
            ),
            None,
        )
    repository.create.assert_called_once()
    repository.activate_source.assert_not_called()

    repository.create.side_effect = RuntimeError("database details")
    with pytest.raises(RuntimeError):
        service(repository, objects).submit(
            JobRequest(
                owner_id=uuid4(),
                source=b"private",
                template_id=uuid4(),
                template_version_id=uuid4(),
                output=JobOutput.DOCX,
                component_versions=COMPONENT_VERSIONS,
                now=NOW,
            ),
            None,
        )
    assert objects.put.call_count == 1


def test_visibility_cancellation_pagination_and_idempotency_validation(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    instance = service(repository, mocker.Mock(spec=ObjectStore))
    visible = job()
    repository.get.return_value = visible
    assert (
        instance.get_visible(
            visible.id, actor_id=visible.owner_id, actor_is_admin=False
        )
        is visible
    )
    with pytest.raises(JobNotFoundError):
        instance.get_visible(visible.id, actor_id=uuid4(), actor_is_admin=False)
    assert (
        instance.get_visible(visible.id, actor_id=uuid4(), actor_is_admin=True)
        is visible
    )
    repository.request_cancel.return_value = None
    with pytest.raises(JobNotFoundError):
        instance.cancel(
            visible.id,
            actor_id=visible.owner_id,
            actor_is_admin=False,
            now=NOW,
        )
    repository.request_cancel.assert_called_once_with(
        visible.id,
        visible.owner_id,
        NOW,
        NOW + timedelta(seconds=RETENTION_SECONDS),
    )
    repository.request_cancel.return_value = visible
    assert (
        instance.cancel(
            visible.id,
            actor_id=uuid4(),
            actor_is_admin=True,
            now=NOW,
        )
        is visible
    )
    with pytest.raises(ValueError):
        instance.list_owner(visible.owner_id, offset=-1, limit=0)
    repository.list_owner.return_value = mocker.sentinel.page
    assert (
        instance.list_owner(visible.owner_id, offset=0, limit=1) is mocker.sentinel.page
    )
    with pytest.raises(ValueError):
        instance.submit(
            JobRequest(
                owner_id=visible.owner_id,
                source=b"source",
                template_id=visible.template_id,
                template_version_id=visible.template_version_id,
                output=visible.output,
                component_versions=COMPONENT_VERSIONS,
                now=NOW,
            ),
            "contains space",
        )


def test_result_download_and_policy_validation(mocker: MockerFixture) -> None:
    repository = mocker.Mock(spec=JobRepository)
    objects = mocker.Mock(spec=ObjectStore)
    instance = service(repository, objects)
    succeeded = job(
        state=JobState.SUCCEEDED,
        step=JobStep.COMPLETE,
        progress=100,
        result_object_id=uuid4(),
    )
    repository.get.return_value = succeeded
    objects.get.return_value = b"result"
    assert instance.download(succeeded.id, actor_id=uuid4(), actor_is_admin=True) == (
        succeeded,
        b"result",
    )

    repository.get.return_value = job()
    with pytest.raises(JobConflictError):
        instance.download(succeeded.id, actor_id=uuid4(), actor_is_admin=True)
    repository.get.return_value = succeeded
    objects.get.side_effect = ObjectNotFoundError
    with pytest.raises(JobConflictError):
        instance.download(
            succeeded.id, actor_id=succeeded.owner_id, actor_is_admin=False
        )

    for invalid in (0, True):
        with pytest.raises(ValueError):
            JobServicePolicy(invalid)
