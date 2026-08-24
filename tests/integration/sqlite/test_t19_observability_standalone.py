"""Real SQLite observability and immutable-audit query coverage."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import insert

from md_converter.auth.models import Role, User
from md_converter.jobs.errors import JobRepositoryError
from md_converter.jobs.models import JobOutput, JobRequest
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.observability import (
    SqlAuditReader,
    SqlOperationalObserver,
)
from md_converter.persistence.schema import TemplateAuditRow
from md_converter.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from md_converter.storage import FilesystemObjectStore
from tests.template_records import publish_template_pair

pytestmark = pytest.mark.integration


def test_sqlite_queue_metrics_correlation_and_audit_are_content_free(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    template_id, version_id = uuid4(), uuid4()
    publish_template_pair(engine, owner.id, template_id, version_id)
    repository = SqlJobRepository(engine)
    service = JobService(
        repository, FilesystemObjectStore(tmp_path), JobServicePolicy(3_600)
    )
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)

    first, _ = service.submit(
        JobRequest(
            owner.id,
            b"# private markdown",
            template_id,
            version_id,
            JobOutput.DOCX,
            (("md-converter", "0.1.0"),),
            now,
            "request-standalone",
        ),
        None,
    )
    service.submit(
        JobRequest(
            owner.id,
            b"# other private markdown",
            template_id,
            version_id,
            JobOutput.PDF,
            (("md-converter", "0.1.0"),),
            now + timedelta(seconds=2),
            "request-second",
        ),
        None,
    )
    snapshot = SqlOperationalObserver(engine).observe_queue(now + timedelta(seconds=10))
    assert (snapshot.depth, snapshot.oldest_age_seconds, snapshot.active_jobs) == (
        2,
        10.0,
        0,
    )
    claimed = repository.claim(
        "observer-worker", now + timedelta(seconds=11), now + timedelta(seconds=41)
    )
    assert claimed is not None and claimed.id == first.id
    assert claimed.correlation_id == "request-standalone"
    running = SqlOperationalObserver(engine).observe_queue(now + timedelta(seconds=12))
    assert (running.depth, running.active_jobs) == (1, 1)

    audit = SqlAuditReader(engine).list_recent(offset=0, limit=10)
    assert len(audit) == 0
    with engine.begin() as connection:
        connection.execute(
            insert(TemplateAuditRow).values(
                id=str(uuid4()),
                actor_id=str(owner.id),
                owner_id=str(owner.id),
                template_id=str(template_id),
                operation="replace",
                version_id=str(version_id),
                administrator_intervention=False,
                created_at=now,
            )
        )
    record = SqlAuditReader(engine).list_recent(offset=0, limit=1)[0]
    assert record.operation == "replace"
    assert record.target_id == template_id
    assert record.version_id == version_id
    assert not hasattr(record, "content")
    assert not hasattr(record, "filename")


def test_sqlite_observation_failure_is_sanitized() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    with pytest.raises(JobRepositoryError):
        SqlOperationalObserver(engine).observe_queue(datetime.now(UTC))
    with pytest.raises(PersistenceError):
        SqlAuditReader(engine).list_recent(offset=0, limit=10)
    with pytest.raises(ValueError, match="pagination"):
        SqlAuditReader(engine).list_recent(offset=-1, limit=10)
