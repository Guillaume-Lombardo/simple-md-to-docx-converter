"""Library repository boundaries over real SQLite metadata and filesystem objects."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import Engine, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from markweave.persistence.composer.library import (
    LibraryArtifactError,
    LibraryConflictError,
    LibraryNotFoundError,
    LibrarySource,
    SqlComposerLibraryRepository,
)
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import UserRow
from markweave.persistence.sql import create_database_engine
from markweave.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStoreError,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_SOURCE = LibrarySource(b"# Notes\n", "text/markdown", "test:library", ("# Notes",))


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    created = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(created)
    try:
        yield created
    finally:
        created.dispose()


def _user(engine: Engine, *, active: bool = True) -> UUID:
    identity = uuid4()
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(identity),
                username=identity.hex,
                normalized_username=identity.hex,
                password_hash="hash",  # noqa: S106 - isolated fixture
                role="user",
                active=active,
                auth_version=0,
                password_change_required=False,
            )
        )
    return identity


def _repository(engine: Engine, tmp_path: Path) -> SqlComposerLibraryRepository:
    return SqlComposerLibraryRepository(
        engine,
        FilesystemObjectStore(tmp_path / "objects"),
        publication_lease=timedelta(seconds=30),
        maximum_scanned_chunks=10,
    )


def test_limits_titles_and_sources_are_validated(
    engine: Engine, tmp_path: Path
) -> None:
    objects = FilesystemObjectStore(tmp_path)
    for lease, scanned in ((timedelta(0), 1), (timedelta(seconds=1), 0)):
        with pytest.raises(ValueError, match="limits"):
            SqlComposerLibraryRepository(
                engine, objects, publication_lease=lease, maximum_scanned_chunks=scanned
            )
    repository = _repository(engine, tmp_path)
    owner = _user(engine)
    for title in ("", "   ", "x" * 201):
        with pytest.raises(ValueError, match="title"):
            repository.create(owner, title, _SOURCE)
    for source in (
        replace(_SOURCE, content=b""),
        replace(_SOURCE, scan_receipt=" "),
        replace(_SOURCE, media_type=""),
        replace(_SOURCE, chunks=("",)),
    ):
        with pytest.raises(ValueError, match="source"):
            repository.create(owner, "Notes", source)
    with pytest.raises(ValueError, match="search"):
        repository.search(owner, (), limit=1)
    with pytest.raises(ValueError, match="Recovery"):
        repository.recover_pending(limit=0)


def test_owners_and_grants_require_active_users(engine: Engine, tmp_path: Path) -> None:
    repository = _repository(engine, tmp_path)
    owner, reader = _user(engine), _user(engine)
    inactive = _user(engine, active=False)
    with pytest.raises(LibraryNotFoundError):
        repository.create(inactive, "Notes", _SOURCE)
    document = repository.create(owner, "Notes", _SOURCE)
    for target, error in (
        (owner, LibraryConflictError),
        (inactive, LibraryNotFoundError),
        (uuid4(), LibraryNotFoundError),
    ):
        with pytest.raises(error):
            repository.grant(owner, document.id, target, if_match=document.etag)
    with pytest.raises(LibraryConflictError):
        repository.revoke(owner, document.id, reader, if_match=document.etag)
    shared = repository.grant(owner, document.id, reader, if_match=document.etag)
    with pytest.raises(LibraryConflictError):
        repository.grant(owner, document.id, reader, if_match=shared.etag)
    with pytest.raises(LibraryNotFoundError):
        repository.grant(reader, document.id, owner, if_match=shared.etag)

    with Session(engine) as database, database.begin():
        database.execute(
            update(UserRow).where(UserRow.id == str(reader)).values(active=False)
        )
    assert repository.list_visible(reader) == ()
    with pytest.raises(LibraryNotFoundError):
        repository.get(reader, document.id)
    with pytest.raises(LibraryNotFoundError):
        repository.excerpts(reader, ((document.current_version_id, 0),))


def test_downloads_verify_exact_bytes(engine: Engine, tmp_path: Path) -> None:
    repository = _repository(engine, tmp_path)
    owner = _user(engine)
    document = repository.create(owner, "Notes", _SOURCE)
    key = ObjectKey(ObjectScope.COMPOSER_LIBRARY, owner, document.current_version_id)
    objects = FilesystemObjectStore(tmp_path / "objects")

    assert repository.download(owner, document.id, document.current_version_id)[1] == (
        _SOURCE.content
    )
    with pytest.raises(LibraryNotFoundError):
        repository.download(owner, document.id, uuid4())
    objects.put(key, b"# Tampered\n")
    with pytest.raises(LibraryArtifactError, match="integrity"):
        repository.download(owner, document.id, document.current_version_id)
    objects.delete(key)
    with pytest.raises(LibraryArtifactError, match="unavailable"):
        repository.download(owner, document.id, document.current_version_id)


def test_publication_detects_a_corrupt_object_write(
    engine: Engine, tmp_path: Path, mocker: MockerFixture
) -> None:
    repository = _repository(engine, tmp_path)
    owner = _user(engine)
    objects = repository._objects
    mocker.patch.object(objects, "get", return_value=b"other")

    with pytest.raises(LibraryArtifactError, match="integrity"):
        repository.create(owner, "Notes", _SOURCE)

    mocker.stopall()
    assert repository.list_visible(owner) == ()


def test_deletion_survives_object_removal_failures(
    engine: Engine, tmp_path: Path, mocker: MockerFixture
) -> None:
    repository = _repository(engine, tmp_path)
    owner = _user(engine)
    document = repository.create(owner, "Notes", _SOURCE)
    mocker.patch.object(
        repository._objects,
        "delete",
        side_effect=ObjectStoreError("unavailable"),
    )

    repository.delete(owner, document.id, if_match=document.etag)

    assert repository.list_visible(owner) == ()


def test_recovery_skips_unavailable_objects(
    engine: Engine, tmp_path: Path, mocker: MockerFixture
) -> None:
    repository = _repository(engine, tmp_path)
    owner = _user(engine)
    mocker.patch.object(repository, "_finalize", side_effect=RuntimeError("crash"))
    with pytest.raises(RuntimeError):
        repository.create(owner, "Notes", _SOURCE)
    mocker.stopall()
    later = SqlComposerLibraryRepository(
        engine,
        repository._objects,
        publication_lease=timedelta(seconds=30),
        maximum_scanned_chunks=10,
        clock=lambda: repository._clock() + timedelta(hours=1),
    )
    mocker.patch.object(
        later._objects,
        "get",
        side_effect=ObjectStoreError("unavailable"),
    )
    assert later.recover_pending(limit=5) == 0
    mocker.stopall()
    assert later.recover_pending(limit=5) == 1
    assert [item.title for item in later.list_visible(owner)] == ["Notes"]


def test_database_failures_become_persistence_errors(
    engine: Engine, tmp_path: Path, mocker: MockerFixture
) -> None:
    repository = _repository(engine, tmp_path)
    owner = _user(engine)
    document = repository.create(owner, "Notes", _SOURCE)
    mocker.patch(
        "markweave.persistence.composer.library.Session",
        side_effect=OperationalError("statement", {}, Exception("down")),
    )
    calls = (
        lambda: repository.create(owner, "Notes", _SOURCE),
        lambda: repository.replace(owner, document.id, _SOURCE, if_match=document.etag),
        lambda: repository.get(owner, document.id),
        lambda: repository.list_visible(owner),
        lambda: repository.list_versions(owner, document.id),
        lambda: repository.download(owner, document.id, document.current_version_id),
        lambda: repository.grant(owner, document.id, uuid4(), if_match='"1"'),
        lambda: repository.delete(owner, document.id, if_match='"1"'),
        lambda: repository.search(owner, ("notes",), limit=1),
        lambda: repository.excerpts(owner, ((document.current_version_id, 0),)),
    )
    for call in calls:
        with pytest.raises(PersistenceError):
            call()
