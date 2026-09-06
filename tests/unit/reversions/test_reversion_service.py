"""Unit coverage for the owner-only reverse-job service."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobNotFoundError,
    ReversionJobRequestError,
    ReversionJobStorageError,
)
from markweave.reversion_jobs.models import (
    ANYDOC_COMPONENT,
    ReversionJob,
    ReversionJobState,
    ReversionJobStep,
    ReversionRequest,
    ReversionSubmission,
)
from markweave.reversion_jobs.service import ReversionService, ReversionServicePolicy
from markweave.reversions.formats import FormatAdmission, FormatFamily
from markweave.storage import (
    BoundedObjectStore,
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStoreError,
    ObjectTooLargeError,
)
from tests.reversion_job_repository_contracts import NOW, RETENTION_END

pytestmark = pytest.mark.unit

SOURCE = b"source"
ADMISSION = FormatAdmission(FormatFamily.WORD, ".docx", "docx", "docx")


def _request(**changes: Any) -> ReversionRequest:
    return ReversionRequest(
        owner_id=changes.pop("owner_id", uuid4()),
        source_stem=changes.pop("source_stem", "report"),
        admission=changes.pop("admission", ADMISSION),
        component_versions=changes.pop("component_versions", (ANYDOC_COMPONENT,)),
        now=changes.pop("now", NOW),
        source=changes.pop("source", SOURCE),
        correlation_id=changes.pop(
            "correlation_id", "12345678-1234-4234-8234-123456789abc"
        ),
        **changes,
    )


def _job(
    submission: ReversionSubmission, *, source_ready: bool = False
) -> ReversionJob:
    return ReversionJob(
        id=submission.id,
        owner_id=submission.owner_id,
        source_object_id=submission.source_object_id,
        source_stem=submission.source_stem,
        admission=submission.admission,
        source_sha256=submission.source_sha256,
        source_size=submission.source_size,
        component_versions=submission.component_versions,
        request_digest=submission.request_digest,
        idempotency_digest=submission.idempotency_digest,
        correlation_id=submission.correlation_id,
        state=ReversionJobState.QUEUED,
        step=ReversionJobStep.QUEUED,
        created_at=submission.created_at,
        updated_at=submission.created_at,
        source_ready=source_ready,
    )


def _service(
    mocker: MockerFixture,
) -> tuple[ReversionService, Any, Any]:
    repository = mocker.Mock(spec=SqlReversionJobRepository)
    objects = mocker.Mock(spec=BoundedObjectStore)
    service = ReversionService(repository, objects, ReversionServicePolicy(3600, 32))
    return service, repository, objects


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_id": "not-a-uuid"},
        {"source_stem": "../unsafe"},
        {"admission": "not-an-admission"},
        {"component_versions": ()},
        {"source": b""},
        {"correlation_id": "unsafe..correlation"},
    ],
)
def test_request_rejects_invalid_domain_fields(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _request(**changes)


@pytest.mark.parametrize("retention", [True, float("inf"), 0])
def test_policy_rejects_invalid_result_retention(retention: float) -> None:
    with pytest.raises(ValueError):
        ReversionServicePolicy(retention, 32)


@pytest.mark.parametrize("maximum", [True, 0])
def test_policy_rejects_invalid_source_limit(maximum: int) -> None:
    with pytest.raises(ValueError):
        ReversionServicePolicy(3_600, maximum)


def test_submit_reserves_stores_and_activates_exact_source(
    mocker: MockerFixture,
) -> None:
    service, repository, objects = _service(mocker)
    created: ReversionJob | None = None

    def create(submission: ReversionSubmission) -> tuple[ReversionJob, bool]:
        nonlocal created
        created = _job(submission)
        return created, False

    repository.create.side_effect = create
    repository.activate_source.side_effect = lambda _job_id, _now: replace(
        cast(ReversionJob, created), source_ready=True
    )
    request = _request()

    job, replayed = service.submit(request, "stable-key")

    assert not replayed and job.source_ready
    submission = repository.create.call_args.args[0]
    assert submission.owner_id == request.owner_id
    assert submission.source_sha256 == sha256(SOURCE).hexdigest()
    assert submission.source_size == len(SOURCE)
    assert submission.idempotency_digest == sha256(b"stable-key").hexdigest()
    objects.put.assert_called_once_with(
        ObjectKey(
            ObjectScope.REVERSION_UPLOAD,
            request.owner_id,
            submission.source_object_id,
        ),
        SOURCE,
    )
    repository.activate_source.assert_called_once_with(submission.id, NOW)


def test_submit_replays_ready_source_without_rewriting_object(
    mocker: MockerFixture,
) -> None:
    service, repository, objects = _service(mocker)
    request = _request()
    first_submission: ReversionSubmission | None = None

    def create(submission: ReversionSubmission) -> tuple[ReversionJob, bool]:
        nonlocal first_submission
        if first_submission is None:
            first_submission = submission
        return _job(first_submission, source_ready=True), True

    repository.create.side_effect = create

    replayed, was_replayed = service.submit(request, "stable-key")

    assert was_replayed and replayed.source_ready
    objects.put.assert_not_called()
    repository.activate_source.assert_not_called()


def test_submit_replays_pending_source_and_finishes_activation(
    mocker: MockerFixture,
) -> None:
    service, repository, objects = _service(mocker)
    request = _request()

    def create(submission: ReversionSubmission) -> tuple[ReversionJob, bool]:
        return _job(submission), True

    repository.create.side_effect = create

    def activate(job_id, _now):
        submission: ReversionSubmission = repository.create.call_args.args[0]
        assert job_id == submission.id
        return _job(submission, source_ready=True)

    repository.activate_source.side_effect = activate

    activated, replayed = service.submit(request, "stable-key")

    assert replayed and activated.id == repository.create.call_args.args[0].id
    objects.put.assert_called_once()


def test_submit_rejects_size_idempotency_and_storage_failures(
    mocker: MockerFixture,
) -> None:
    service, repository, objects = _service(mocker)
    with pytest.raises(ReversionJobRequestError):
        service.submit(_request(source=b"x" * 33), None)
    with pytest.raises(ValueError):
        service.submit(_request(), "bad\nkey")

    request = _request()
    repository.create.side_effect = lambda submission: (_job(submission), False)
    objects.put.side_effect = ObjectStoreError("sanitized")
    with pytest.raises(ReversionJobStorageError):
        service.submit(request, None)
    objects.delete.assert_called_once()


@pytest.mark.parametrize("key", ["", "x" * 256])
def test_submit_rejects_other_invalid_idempotency_keys(
    key: str, mocker: MockerFixture
) -> None:
    service, _repository, _objects = _service(mocker)

    with pytest.raises(ValueError):
        service.submit(_request(), key)


def test_submit_preserves_unexpected_storage_failure_after_compensation(
    mocker: MockerFixture,
) -> None:
    service, repository, objects = _service(mocker)
    repository.create.side_effect = lambda submission: (_job(submission), False)
    objects.put.side_effect = RuntimeError("unexpected")
    objects.delete.side_effect = RuntimeError("cleanup")

    with pytest.raises(RuntimeError, match="unexpected"):
        service.submit(_request(), None)


def test_replay_rejects_a_different_request_digest(mocker: MockerFixture) -> None:
    service, repository, _objects = _service(mocker)
    request = _request()

    def conflict(submission: ReversionSubmission) -> tuple[ReversionJob, bool]:
        return replace(
            _job(submission, source_ready=True), request_digest="f" * 64
        ), True

    repository.create.side_effect = conflict

    with pytest.raises(ReversionJobConflictError):
        service.submit(request, "stable-key")


def test_owner_lifecycle_never_accepts_an_administrator_bypass(
    mocker: MockerFixture,
) -> None:
    service, repository, _objects = _service(mocker)
    owner_id = uuid4()
    other_id = uuid4()
    job_id = uuid4()
    repository.get_owner.side_effect = lambda candidate, owner: (
        None if candidate != job_id or owner != owner_id else mocker.sentinel.job
    )

    assert service.get(job_id, owner_id) is mocker.sentinel.job
    with pytest.raises(ReversionJobNotFoundError):
        service.get(job_id, other_id)
    repository.get_owner.assert_called_with(job_id, other_id)


def test_cancel_uses_exact_owner_and_reverse_retention(mocker: MockerFixture) -> None:
    service, repository, _objects = _service(mocker)
    owner_id = uuid4()
    job_id = uuid4()
    repository.get_owner.return_value = mocker.sentinel.visible
    repository.request_cancel.return_value = mocker.sentinel.cancelled

    assert service.cancel(job_id, owner_id, now=NOW) is mocker.sentinel.cancelled
    repository.request_cancel.assert_called_once_with(
        job_id, owner_id, NOW, RETENTION_END
    )


def test_list_validates_pagination_and_delegates_owner_predicate(
    mocker: MockerFixture,
) -> None:
    service, repository, _objects = _service(mocker)
    owner_id = uuid4()
    repository.list_owner.return_value = mocker.sentinel.page

    with pytest.raises(ValueError):
        service.list_owner(owner_id, offset=-1, limit=1)
    with pytest.raises(ValueError):
        service.list_owner(owner_id, offset=0, limit=0)
    assert service.list_owner(owner_id, offset=2, limit=3) is mocker.sentinel.page
    repository.list_owner.assert_called_once_with(owner_id, offset=2, limit=3)


def test_cancel_fails_closed_when_owner_row_disappears_during_transition(
    mocker: MockerFixture,
) -> None:
    service, repository, _objects = _service(mocker)
    repository.get_owner.return_value = mocker.sentinel.visible
    repository.request_cancel.return_value = None

    with pytest.raises(ReversionJobNotFoundError):
        service.cancel(uuid4(), uuid4(), now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": ReversionJobState.QUEUED},
        {"result_object_id": None},
        {"result_size": None},
        {"result_sha256": None},
    ],
)
def test_download_rejects_every_incomplete_publication_field(
    changes: dict[str, object], mocker: MockerFixture
) -> None:
    service, repository, _objects = _service(mocker)
    owner_id = uuid4()
    visible = mocker.Mock(
        state=ReversionJobState.SUCCEEDED,
        result_object_id=uuid4(),
        result_size=4,
        result_sha256=sha256(b"good").hexdigest(),
        owner_id=owner_id,
    )
    for field_name, value in changes.items():
        setattr(visible, field_name, value)
    repository.get_owner.return_value = visible

    with pytest.raises(ReversionJobConflictError):
        service.download(uuid4(), owner_id)


@pytest.mark.parametrize(
    "failure",
    [
        ObjectNotFoundError("missing"),
        ObjectTooLargeError("large"),
        ObjectStoreError("store"),
    ],
)
def test_download_fails_closed_for_missing_or_invalid_result_storage(
    failure: Exception,
    mocker: MockerFixture,
) -> None:
    service, repository, objects = _service(mocker)
    owner_id = uuid4()
    result_id = uuid4()
    visible = mocker.Mock(
        state=ReversionJobState.SUCCEEDED,
        result_object_id=result_id,
        result_size=4,
        result_sha256=sha256(b"good").hexdigest(),
        owner_id=owner_id,
    )
    repository.get_owner.return_value = visible
    objects.get_bounded.side_effect = failure

    expected = (
        ReversionJobConflictError
        if isinstance(failure, ObjectNotFoundError)
        else ReversionJobStorageError
    )
    with pytest.raises(expected):
        service.download(uuid4(), owner_id)


def test_download_verifies_exact_size_and_digest(mocker: MockerFixture) -> None:
    service, repository, objects = _service(mocker)
    owner_id = uuid4()
    result_id = uuid4()
    visible = mocker.Mock(
        state=ReversionJobState.SUCCEEDED,
        result_object_id=result_id,
        result_size=4,
        result_sha256=sha256(b"good").hexdigest(),
        owner_id=owner_id,
    )
    repository.get_owner.return_value = visible
    objects.get_bounded.return_value = b"evil"

    with pytest.raises(ReversionJobStorageError):
        service.download(uuid4(), owner_id)

    objects.get_bounded.return_value = b"good"
    job, content = service.download(uuid4(), owner_id)
    assert job is visible and content == b"good"
