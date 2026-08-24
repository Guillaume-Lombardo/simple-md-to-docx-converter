"""Unit control-flow coverage for bounded SQL observability adapters."""

from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from typing import Any
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


def _engine_connection(mocker: MockerFixture, engine: Any):
    context = mocker.MagicMock()
    engine.connect.return_value = context
    return context.__enter__.return_value


def test_queue_observer_maps_aggregate_rows_and_sanitizes_failure(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)
    database = _engine_connection(mocker, engine)
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


def test_queue_observer_enforces_driver_deadline_and_cancels_active_calls(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    engine.dialect.name = "sqlite"
    database = _engine_connection(mocker, engine)
    driver = database.connection.driver_connection
    observer = SqlOperationalObserver(engine)
    result = mocker.Mock()
    result.one.return_value = (0, None, 0)

    def execute_with_cancellation(_statement: object):
        observer.cancel_observations(timeout_seconds=0.25)
        return result

    database.execute.side_effect = execute_with_cancellation

    observer.observe_queue(datetime(2026, 8, 24, 20, tzinfo=UTC), timeout_seconds=0.5)
    assert driver.set_progress_handler.call_count == 2
    callback = driver.set_progress_handler.call_args_list[0].args[0]
    assert callback() in {0, 1}
    assert driver.set_progress_handler.call_args_list[1].args == (None, 0)
    driver.interrupt.assert_called_once_with()

    for invalid in (True, 0, float("inf")):
        with pytest.raises(ValueError, match="timeout"):
            observer.observe_queue(
                datetime(2026, 8, 24, 20, tzinfo=UTC),
                timeout_seconds=invalid,
            )


def test_queue_observer_applies_postgresql_timeout_and_driver_cancel(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    engine.dialect.name = "postgresql"
    database = _engine_connection(mocker, engine)
    driver = database.connection.driver_connection
    driver.interrupt = None
    observer = SqlOperationalObserver(engine, default_timeout_seconds=0.5)
    aggregate = mocker.Mock()
    aggregate.one.return_value = (0, None, 0)

    def execute(statement: object, _parameters: object = None):
        if database.execute.call_count == 2:
            observer.cancel_observations(timeout_seconds=0.25)
            return aggregate
        return mocker.Mock()

    database.execute.side_effect = execute

    observer.observe_queue(datetime(2026, 8, 24, 20, tzinfo=UTC))
    assert "set_config" in str(database.execute.call_args_list[0].args[0])
    driver.cancel_safe.assert_called_once_with(timeout=0.25)


def test_queue_observer_rejects_cancelled_or_unavailable_database_boundary(
    mocker: MockerFixture,
) -> None:
    for invalid in (True, 0, float("inf")):
        with pytest.raises(ValueError, match="timeout"):
            SqlOperationalObserver(mocker.Mock(), default_timeout_seconds=invalid)

    engine = mocker.Mock()
    database = _engine_connection(mocker, engine)
    cancelled = Event()
    cancelled.set()
    observer = SqlOperationalObserver(engine)
    with pytest.raises(JobRepositoryError):
        observer.observe_queue(
            datetime(2026, 8, 24, 20, tzinfo=UTC), cancelled=cancelled
        )
    database.execute.assert_not_called()

    engine.connect.side_effect = SQLAlchemyError()
    with pytest.raises(JobRepositoryError):
        observer.observe_queue(datetime(2026, 8, 24, 20, tzinfo=UTC))


def test_queue_observer_supports_driver_without_optional_interrupt_hooks(
    mocker: MockerFixture,
) -> None:
    engine = mocker.Mock()
    engine.dialect.name = "other"
    database = _engine_connection(mocker, engine)
    driver = database.connection.driver_connection
    driver.interrupt = None
    driver.cancel_safe = None
    driver.set_progress_handler = None
    observer = SqlOperationalObserver(engine)
    result = mocker.Mock()
    result.one.return_value = (0, None, 0)

    def execute(_statement: object):
        observer.cancel_observations(timeout_seconds=0.25)
        return result

    database.execute.side_effect = execute
    assert (
        observer.observe_queue(
            datetime(2026, 8, 24, 20, tzinfo=UTC), timeout_seconds=0.5
        ).depth
        == 0
    )


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
            target_id=str(target_id),
            target_type="template",
            target_version=str(version_id),
            operation="replace",
            version_id=str(version_id),
            administrator_intervention=True,
            created_at=now,
        ),
        SimpleNamespace(
            id=str(uuid4()),
            actor_id=str(actor_id),
            owner_id=str(owner_id),
            target_id=str(target_id),
            target_type="user",
            target_version="3",
            operation="user_password_reset",
            version_id=None,
            administrator_intervention=False,
            created_at=now.replace(tzinfo=None),
        ),
    )
    database.execute.return_value = rows
    reader = SqlAuditReader(engine)

    records = reader.list_recent(offset=0, limit=2)
    assert records[0].version_id == version_id
    assert records[1].version_id is None
    assert records[1].target_type == "user"
    assert records[1].target_version == "3"
    assert records[1].created_at.tzinfo is UTC
    with pytest.raises(ValueError, match="pagination"):
        reader.list_recent(offset=0, limit=0)

    database.execute.side_effect = SQLAlchemyError()
    with pytest.raises(PersistenceError):
        reader.list_recent(offset=0, limit=1)

    database.execute.side_effect = None
    database.execute.return_value = (SimpleNamespace(**{**vars(rows[0]), "id": "bad"}),)
    with pytest.raises(PersistenceError):
        reader.list_recent(offset=0, limit=1)
