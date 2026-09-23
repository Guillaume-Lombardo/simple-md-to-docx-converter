"""Mixed-version queued jobs fail before a newer worker touches document engines."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.auth.models import Role, User
from markweave.conversion.errors import ConversionErrorCode
from markweave.jobs.errors import JobConflictError
from markweave.jobs.models import JobOutput, JobProcessResult, JobRequest, JobState
from markweave.jobs.service import JobService, JobServicePolicy
from markweave.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from markweave.storage import FilesystemObjectStore

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def test_old_queued_job_fails_before_processor_and_matching_job_succeeds(
    tmp_path, mocker: MockerFixture
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    try:
        owner = User(uuid4(), "Owner", "worker-version-owner", "hash", Role.USER)
        SqlUserRepository(engine).create(owner)
        repository = SqlJobRepository(engine)
        objects = FilesystemObjectStore(tmp_path)
        service = JobService(repository, objects, JobServicePolicy(60))
        old_manifest = (("md-converter", "old-release"),)
        new_manifest = (("md-converter", "new-release"),)
        now = datetime.now(UTC)
        old, _ = service.submit(
            JobRequest(
                owner.id,
                b"# Old approved source\n",
                None,
                None,
                JobOutput.DOCX,
                old_manifest,
                now,
            ),
            "old-runtime-submission",
        )
        processor = mocker.Mock()
        processor.process.return_value = JobProcessResult(b"exact-new-result")
        worker = ConversionWorker(
            worker_id="new-runtime-worker",
            runtime=WorkerRuntime(
                repository,
                objects,
                processor,
                lambda: datetime.now(UTC),
                runtime_component_versions=new_manifest,
            ),
            policy=WorkerPolicy(1, 0.05, 60, 1),
        )
        assert worker.run_once()
        failed = repository.get(old.id)
        assert failed is not None
        assert failed.state is JobState.FAILED
        assert failed.error_code == ConversionErrorCode.RUNTIME_VERSION_MISMATCH.value
        assert failed.result_object_id is None
        processor.process.assert_not_called()
        with pytest.raises(JobConflictError):
            service.download(old.id, actor_id=owner.id, actor_is_admin=False)

        matching, _ = service.submit(
            JobRequest(
                owner.id,
                b"# New approved source\n",
                None,
                None,
                JobOutput.DOCX,
                new_manifest,
                now + timedelta(seconds=1),
            ),
            "new-runtime-submission",
        )
        assert worker.run_once()
        finished, content = service.download(
            matching.id, actor_id=owner.id, actor_is_admin=False
        )
        assert finished.state is JobState.SUCCEEDED
        assert content == b"exact-new-result"
        processor.process.assert_called_once()
    finally:
        engine.dispose()
