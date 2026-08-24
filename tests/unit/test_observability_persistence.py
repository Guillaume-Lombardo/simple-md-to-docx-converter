"""Unit control-flow coverage for bounded SQL observability adapters."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy.exc import SQLAlchemyError

from md_converter.jobs.errors import JobRepositoryError
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.observability import (
    SqlAuditReader,
    SqlOperationalObserver,
)

pytestmark = pytest.mark.unit


def _database_session(mocker: MockerFixture):
    session = mocker.patch("md_converter.persistence.observability.DatabaseSession")
    return session.return_value.__enter__.return_value


def test_queue_observer_maps_aggregate_rows_and_sanitizes_failure(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)
    database = _database_session(mocker)
    database.execute.return_value.one.side_effect = (
        (2, now - timedelta(seconds=5), 1),
        (0, None, 0),
        SQLAlchemyError(),
    )
    observer = SqlOperationalObserver(engine)

    assert observer.observe_queue(now).oldest_age_seconds == 5
    assert observer.observe_queue(now).oldest_age_seconds == 0
    with pytest.raises(JobRepositoryError):
        observer.observe_queue(now)


def test_audit_reader_maps_optional_version_and_sanitizes_failure(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    database = _database_session(mocker)
    actor_id, owner_id, target_id, version_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)
    rows = (
        SimpleNamespace(
            id=str(uuid4()),
            actor_id=str(actor_id),
            owner_id=str(owner_id),
            template_id=str(target_id),
            operation="replace",
            version_id=str(version_id),
            administrator_intervention=True,
            created_at=now,
        ),
        SimpleNamespace(
            id=str(uuid4()),
            actor_id=str(actor_id),
            owner_id=str(owner_id),
            template_id=str(target_id),
            operation="archive",
            version_id=None,
            administrator_intervention=False,
            created_at=now.replace(tzinfo=None),
        ),
    )
    database.scalars.return_value = rows
    reader = SqlAuditReader(engine)

    records = reader.list_recent(offset=0, limit=2)
    assert records[0].version_id == version_id
    assert records[1].version_id is None
    assert records[1].created_at.tzinfo is UTC
    with pytest.raises(ValueError, match="pagination"):
        reader.list_recent(offset=0, limit=0)

    database.scalars.side_effect = SQLAlchemyError()
    with pytest.raises(PersistenceError):
        reader.list_recent(offset=0, limit=1)

    database.scalars.side_effect = None
    database.scalars.return_value = (SimpleNamespace(**{**vars(rows[0]), "id": "bad"}),)
    with pytest.raises(PersistenceError):
        reader.list_recent(offset=0, limit=1)
