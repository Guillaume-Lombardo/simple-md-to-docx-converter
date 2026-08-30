"""Fast in-process coverage for the SQL job repository control flow."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy.exc import SQLAlchemyError

from markweave.auth.models import Role, User
from markweave.jobs.errors import JobRepositoryError
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.sql import SqlUserRepository, create_database_engine
from tests.job_repository_contracts import (
    TEMPLATE_ID,
    TEMPLATE_VERSION_ID,
    exercise_job_repository_contract,
    submission,
)
from tests.template_records import publish_template_pair

pytestmark = pytest.mark.unit


def test_inprocess_job_repository_contract() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = User(uuid4(), "Owner", "job-owner", "hash:owner", Role.USER)
    other = User(uuid4(), "Other", "job-other", "hash:other", Role.USER)
    users.create(owner)
    users.create(other)
    publish_template_pair(engine, owner.id, TEMPLATE_ID, TEMPLATE_VERSION_ID)
    exercise_job_repository_contract(SqlJobRepository(engine), owner.id, other.id)
    engine.dispose()


def test_job_repository_rejects_unsupported_dialect(mocker: MockerFixture) -> None:
    engine = mocker.Mock()
    engine.dialect.name = "mysql"
    with pytest.raises(ValueError, match="SQLite or PostgreSQL"):
        SqlJobRepository(engine)


def test_postgresql_job_update_helper_keeps_returning(mocker: MockerFixture) -> None:
    engine = mocker.Mock()
    engine.dialect.name = "postgresql"
    repository = SqlJobRepository(engine)
    database = mocker.Mock()
    statement = mocker.Mock()
    expected = mocker.Mock()
    database.scalar.return_value = expected

    assert repository._update_job_row(database, statement, "job-id") is expected

    statement.returning.assert_called_once()
    database.scalar.assert_called_once_with(statement.returning.return_value)


def test_empty_sqlite_queue_has_no_claim() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    now = datetime(2026, 8, 25, tzinfo=UTC)

    assert (
        SqlJobRepository(engine).claim("worker", now, now + timedelta(seconds=30))
        is None
    )
    engine.dispose()


def test_job_repository_sanitizes_each_bounded_store_failure(
    mocker: MockerFixture,
) -> None:
    engine = mocker.MagicMock()
    engine.dialect.name = "sqlite"
    repository = SqlJobRepository(engine)
    owner_id = uuid4()
    now = datetime(2026, 8, 30, tzinfo=UTC)
    for module in ("claims", "cleanup", "lifecycle", "queries", "submission"):
        mocker.patch(
            f"markweave.persistence.jobs.{module}.DatabaseSession",
            side_effect=SQLAlchemyError("private SQL and values"),
        )

    operations = (
        lambda: repository.create(submission(owner_id)),
        lambda: repository.get(uuid4()),
        lambda: repository.claim("worker", now, now + timedelta(seconds=30)),
        lambda: repository.request_cancel(
            uuid4(), owner_id, now, now + timedelta(hours=1)
        ),
        lambda: repository.expire_terminal(
            "worker", now, now + timedelta(seconds=30), 1
        ),
    )
    for operation in operations:
        with pytest.raises(JobRepositoryError) as caught:
            operation()
        assert "private" not in repr(caught.value)
        assert caught.value.__suppress_context__
