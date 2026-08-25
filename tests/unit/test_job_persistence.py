"""Fast in-process coverage for the SQL job repository control flow."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from md_converter.auth.models import Role, User
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from tests.job_repository_contracts import (
    TEMPLATE_ID,
    TEMPLATE_VERSION_ID,
    exercise_job_repository_contract,
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
