"""Real SQLite and atomic-files integration coverage."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import delete, text

from md_converter.app import create_app
from md_converter.auth.errors import AuthenticationError
from md_converter.auth.models import Role, Session, User
from md_converter.config import Settings
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import UserRow
from md_converter.persistence.sql import (
    DatabaseReadinessProbe,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from md_converter.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStoreError,
)
from tests.storage_contracts import (
    exercise_auth_repository_contract,
    exercise_object_store_contract,
)


@pytest.mark.integration
def test_sqlite_authentication_repository_contract(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    assert DatabaseReadinessProbe(engine).is_ready()
    exercise_auth_repository_contract(
        SqlUserRepository(engine), SqlSessionRepository(engine)
    )
    engine.dispose()


@pytest.mark.integration
def test_sqlite_enforces_foreign_keys_on_reopened_connections_and_cascades(
    tmp_path: Path,
) -> None:
    database_url = standalone_database_url(tmp_path)
    engine = create_database_engine(database_url)
    upgrade_database(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    engine.dispose()

    reopened = create_database_engine(database_url)
    with reopened.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
    users = SqlUserRepository(reopened)
    sessions = SqlSessionRepository(reopened)
    user = User(uuid4(), "Cascade", "cascade", "hash:cascade", Role.USER)
    users.create(user)
    now = datetime(2026, 8, 23, tzinfo=UTC)
    valid_session = Session(
        token_digest="a" * 64,
        csrf_digest="b" * 64,
        user_id=user.id,
        auth_version=0,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
    )
    sessions.create(valid_session)
    missing_user_digest = "c" * 64
    with pytest.raises(PersistenceError) as caught:
        sessions.create(
            Session(
                token_digest=missing_user_digest,
                csrf_digest="d" * 64,
                user_id=uuid4(),
                auth_version=0,
                created_at=now,
                last_seen_at=now,
                idle_expires_at=now + timedelta(minutes=30),
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
    assert missing_user_digest not in repr(caught.value)
    with reopened.begin() as connection:
        connection.execute(delete(UserRow).where(UserRow.id == str(user.id)))
    assert sessions.get(valid_session.token_digest) is None
    reopened.dispose()


@pytest.mark.integration
def test_standalone_restart_preserves_admin_password_and_sessions(
    tmp_path: Path,
) -> None:
    data_directory = tmp_path / "space ?#% données"
    original_password = "original-" + "password"
    replacement_password = "replacement-" + "password"
    first_settings = Settings(
        initial_admin_username="admin",
        initial_admin_password=original_password,
        argon2_memory_cost=8,
        argon2_time_cost=1,
        storage_profile="standalone",
        standalone_data_directory=data_directory,
    )
    first = create_app(first_settings)
    login = first.state.components.authentication.login("admin", original_password)
    database_path = data_directory / "metadata.sqlite3"
    assert database_path.is_file()
    assert set(tmp_path.iterdir()) == {data_directory}

    second_settings = Settings(
        initial_admin_username="ADMIN",
        initial_admin_password=replacement_password,
        argon2_memory_cost=8,
        argon2_time_cost=1,
        storage_profile="standalone",
        standalone_data_directory=data_directory,
    )
    second = create_app(second_settings)
    authentication = second.state.components.authentication
    assert authentication.authenticate(login.session_token).username == "admin"
    assert authentication.login("admin", original_password).user.username == "admin"
    with pytest.raises(AuthenticationError):
        authentication.login("admin", replacement_password)
    assert database_path.is_file()


@pytest.mark.integration
def test_atomic_filesystem_object_store_contract(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path)
    assert store.is_ready()
    exercise_object_store_contract(store)
    assert not list((tmp_path / "objects").rglob(".pending-*"))


@pytest.mark.integration
def test_filesystem_write_failure_is_sanitized_and_leaves_no_partial_object(
    tmp_path: Path,
) -> None:
    store = FilesystemObjectStore(tmp_path)
    key = ObjectKey(ObjectScope.UPLOAD, uuid4(), uuid4())
    scope_path = tmp_path / "objects" / ObjectScope.UPLOAD.value
    scope_path.write_bytes(b"blocks-directory-creation")
    with pytest.raises(ObjectStoreError, match="Object storage operation failed"):
        store.put(key, b"content")
    assert scope_path.read_bytes() == b"blocks-directory-creation"


@pytest.mark.integration
def test_filesystem_replace_failure_removes_the_pending_file(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = FilesystemObjectStore(tmp_path)
    key = ObjectKey(ObjectScope.RESULT, uuid4(), uuid4())
    mocker.patch("md_converter.storage.os.replace", side_effect=PermissionError)
    with pytest.raises(ObjectStoreError, match="Object storage operation failed"):
        store.put(key, b"content")
    assert not list((tmp_path / "objects").rglob(".pending-*"))


@pytest.mark.integration
def test_filesystem_delete_sync_failure_is_sanitized_after_removal(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    store = FilesystemObjectStore(tmp_path / "nested" / "data")
    key = ObjectKey(ObjectScope.RESULT, uuid4(), uuid4())
    store.put(key, b"content")
    mocker.patch("md_converter.storage.os.fsync", side_effect=PermissionError)
    with pytest.raises(ObjectStoreError, match="Object storage operation failed"):
        store.delete(key)
    assert not store.exists(key)
