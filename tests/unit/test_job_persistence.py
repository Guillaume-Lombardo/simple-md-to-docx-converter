"""Fast in-process coverage for the SQL job repository control flow."""

from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from md_converter.auth.models import Role, User
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from tests.job_repository_contracts import exercise_job_repository_contract

pytestmark = pytest.mark.unit


def test_inprocess_job_repository_contract() -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = User(uuid4(), "Owner", "job-owner", "hash:owner", Role.USER)
    other = User(uuid4(), "Other", "job-other", "hash:other", Role.USER)
    users.create(owner)
    users.create(other)
    exercise_job_repository_contract(SqlJobRepository(engine), owner.id, other.id)
    engine.dispose()


def test_job_repository_rejects_unsupported_dialect(mocker: MockerFixture) -> None:
    engine = mocker.Mock()
    engine.dialect.name = "mysql"
    with pytest.raises(ValueError, match="SQLite or PostgreSQL"):
        SqlJobRepository(engine)
