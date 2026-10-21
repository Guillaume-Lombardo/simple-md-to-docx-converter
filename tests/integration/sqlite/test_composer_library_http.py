"""Permissioned, cited library over HTTP with real persistence and object storage.

The scenarios are shared with the PostgreSQL/S3 profile tests.
"""

import hashlib
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.config import Settings
from markweave.malware import MalwareDetectedError, UploadScanner
from markweave.persistence.composer.library import (
    LibraryConflictError,
    LibrarySource,
    SqlComposerLibraryRepository,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ComposerContentAuditRow,
    ComposerLibraryCleanupRow,
    ComposerLibraryVersionRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_ROOT = Path(__file__).resolve().parents[3]
_LIBRARY = "/api/v1/composer/library"
_NOTES = (
    "# Findings\n\nThe northern turbine failed after the storm.\n\n"
    "Inspection confirmed corrosion in the gearbox.\n"
)


@dataclass
class LibraryHarness:
    client: TestClient
    engine: Engine
    objects: ObjectStore
    library: SqlComposerLibraryRepository
    authentication: Any
    scanner: Any
    owner: User
    reader: User
    outsider: User

    def act_as(self, user: User) -> None:
        self.authentication.authenticate.return_value = user


@contextmanager
def library_harness(
    engine: Engine, objects: ObjectStore, mocker: MockerFixture, tmp_path: Path
) -> Generator[LibraryHarness]:
    """Serve the real library routes over one metadata engine and object store."""

    upgrade_database(engine)
    suffix = uuid4().hex[:12]
    users = tuple(
        User(uuid4(), f"{name}-{suffix}", f"{name}-{suffix}", "hash", Role.USER)
        for name in ("owner", "reader", "outsider")
    )
    with Session(engine) as database, database.begin():
        for user in users:
            database.add(
                UserRow(
                    id=str(user.id),
                    username=user.username,
                    normalized_username=user.normalized_username,
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role=user.role.value,
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.bootstrap_admin.return_value = users[0]
    authentication.authenticate.return_value = users[0]
    scanner = mocker.Mock(spec=UploadScanner)
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-password",  # noqa: S106 - isolated fixture
        storage_profile="standalone",
        standalone_data_directory=str(tmp_path),
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_upload_max_bytes=40_000,
        composer_http_request_max_bytes=50_000,
        composer_maximum_request_bytes=4096,
    )
    library = SqlComposerLibraryRepository(
        engine,
        objects,
        publication_lease=timedelta(seconds=30),
        maximum_scanned_chunks=100,
    )
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            scanner=scanner,
            composer_library=library,
        ),
    )
    with TestClient(app, base_url="https://testserver") as client:
        yield LibraryHarness(
            client, engine, objects, library, authentication, scanner, *users
        )


@pytest.fixture
def harness(tmp_path: Path, mocker: MockerFixture) -> Iterator[LibraryHarness]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    try:
        with library_harness(
            engine, FilesystemObjectStore(tmp_path), mocker, tmp_path
        ) as created:
            yield created
    finally:
        engine.dispose()


def _upload(  # noqa: PLR0913 - explicit multipart upload fields
    harness: LibraryHarness,
    filename: str,
    content: bytes,
    *,
    title: str | None = None,
    path: str = _LIBRARY,
    etag: str | None = None,
) -> Any:
    return harness.client.post(
        path,
        files={"file": (filename, content)},
        data={} if title is None else {"title": title},
        headers={"X-CSRF-Token": "csrf", **({"If-Match": etag} if etag else {})},
    )


def _search(harness: LibraryHarness, query: str) -> list[dict[str, Any]]:
    response = harness.client.post(
        f"{_LIBRARY}/search",
        json={"query": query, "limit": 5},
        headers={"X-CSRF-Token": "csrf"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    return response.json()["results"]


def exercise_library_lifecycle(harness: LibraryHarness) -> None:  # noqa: PLR0915 - one complete sharing workflow
    """Upload, search, share, cite, replace, revoke and delete one document."""

    client = harness.client
    created = _upload(harness, "findings.md", _NOTES.encode(), title=" Field notes ")
    assert created.status_code == 201, created.text
    document = created.json()
    assert document["title"] == "Field notes"
    assert created.headers["Location"] == f"{_LIBRARY}/{document['id']}"
    harness.scanner.scan.assert_called_once_with(_NOTES.encode())
    path = f"{_LIBRARY}/{document['id']}"
    version_id = document["current_version_id"]

    hits = _search(harness, "TURBINE storm")
    assert [(hit["document_id"], hit["chunk"]) for hit in hits] == [(document["id"], 0)]
    assert "northern turbine failed" in hits[0]["snippet"]
    assert _search(harness, "turbine banana") == []

    harness.act_as(harness.reader)
    assert client.get(path).status_code == 404
    assert _search(harness, "turbine") == []
    refused = client.post(
        f"{_LIBRARY}/excerpts",
        json={"references": [{"version_id": version_id, "chunk": 0}]},
        headers={"X-CSRF-Token": "csrf"},
    )
    assert refused.status_code == 404
    assert refused.json()["error"]["code"] == "COMPOSER_LIBRARY_NOT_FOUND"

    harness.act_as(harness.owner)
    shared = client.put(
        f"{path}/grants/{harness.reader.id}",
        headers={"X-CSRF-Token": "csrf", "If-Match": document["etag"]},
    )
    assert shared.status_code == 200
    assert shared.json()["shared_with"] == [str(harness.reader.id)]
    assert (
        client.put(
            f"{path}/grants/{harness.outsider.id}",
            headers={"X-CSRF-Token": "csrf", "If-Match": document["etag"]},
        ).status_code
        == 412
    )

    harness.act_as(harness.reader)
    seen = client.get(path).json()
    assert seen["shared_with"] == []
    assert [hit["chunk"] for hit in _search(harness, "corrosion gearbox")] == [0]
    excerpts = client.post(
        f"{_LIBRARY}/excerpts",
        json={"references": [{"version_id": version_id, "chunk": 0}]},
        headers={"X-CSRF-Token": "csrf"},
    )
    assert excerpts.status_code == 200
    excerpt = excerpts.json()["excerpts"][0]
    assert excerpt["version_number"] == 1
    assert excerpt["version_sha256"] == hashlib.sha256(_NOTES.encode()).hexdigest()
    assert "Inspection confirmed corrosion" in excerpt["text"]
    download = client.get(f"{path}/versions/{version_id}/content")
    assert download.content == _NOTES.encode()
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert download.headers["Content-Disposition"].endswith('.md"')
    assert (
        client.post(
            f"{path}/versions",
            files={"file": ("x.md", b"# Taken\n")},
            headers={"X-CSRF-Token": "csrf", "If-Match": shared.json()["etag"]},
        ).status_code
        == 404
    )

    harness.act_as(harness.owner)
    replaced = _upload(
        harness,
        "findings.md",
        b"# Findings\n\nThe southern array is healthy.\n",
        path=f"{path}/versions",
        etag=shared.json()["etag"],
    )
    assert replaced.status_code == 201
    assert replaced.json()["current_version_id"] != version_id
    assert _search(harness, "corrosion") == []
    assert [hit["version_number"] for hit in _search(harness, "southern")] == [2]
    versions = client.get(f"{path}/versions").json()["versions"]
    assert [item["number"] for item in versions] == [1, 2]
    still_cited = client.post(
        f"{_LIBRARY}/excerpts",
        json={"references": [{"version_id": version_id, "chunk": 0}]},
        headers={"X-CSRF-Token": "csrf"},
    )
    assert still_cited.status_code == 200

    revoked = client.delete(
        f"{path}/grants/{harness.reader.id}",
        headers={"X-CSRF-Token": "csrf", "If-Match": replaced.json()["etag"]},
    )
    assert revoked.status_code == 200
    harness.act_as(harness.reader)
    assert client.get(path).status_code == 404
    assert _search(harness, "southern") == []
    assert client.get(f"{path}/versions/{version_id}/content").status_code == 404

    harness.act_as(harness.owner)
    assert (
        client.delete(
            path, headers={"X-CSRF-Token": "csrf", "If-Match": '"1"'}
        ).status_code
        == 412
    )
    assert (
        client.delete(
            path,
            headers={"X-CSRF-Token": "csrf", "If-Match": revoked.json()["etag"]},
        ).status_code
        == 204
    )
    assert client.get(path).status_code == 404
    assert (
        client.post(
            f"{_LIBRARY}/excerpts",
            json={"references": [{"version_id": version_id, "chunk": 0}]},
            headers={"X-CSRF-Token": "csrf"},
        ).status_code
        == 404
    )
    with Session(harness.engine) as database:
        operations = [
            row.operation
            for row in database.scalars(
                select(ComposerContentAuditRow)
                .where(ComposerContentAuditRow.target_id == document["id"])
                .order_by(ComposerContentAuditRow.created_at)
            )
        ]
    assert operations == [
        "library_create",
        "library_grant",
        "library_replace",
        "library_revoke",
        "library_delete",
    ]


def test_library_lifecycle_filters_access_before_text_is_returned(
    harness: LibraryHarness,
) -> None:
    exercise_library_lifecycle(harness)


def exercise_office_and_rejected_sources(harness: LibraryHarness) -> None:
    """Office text is indexed; infected, unsupported and empty sources never persist."""

    client = harness.client
    docx = (_ROOT / "spikes/anydoc/corpus/docx/text.docx").read_bytes()
    pptx = (_ROOT / "spikes/anydoc/corpus/pptx/pres.pptx").read_bytes()
    assert _upload(harness, "report.docx", docx).status_code == 201
    assert _upload(harness, "deck.pptx", pptx).status_code == 201
    assert [hit["title"] for hit in _search(harness, "struck runs")] == ["report"]
    assert [hit["title"] for hit in _search(harness, "speaker note intro")] == ["deck"]
    listed = client.get(f"{_LIBRARY}?query=dec").json()["documents"]
    assert [item["title"] for item in listed] == ["deck"]

    harness.scanner.scan.side_effect = MalwareDetectedError("infected")
    infected = _upload(harness, "virus.md", b"# Infected\n")
    assert infected.status_code == 422
    assert infected.json()["error"]["code"] == "UPLOAD_MALWARE_DETECTED"
    harness.scanner.scan.side_effect = None
    for filename, content, status, code in (
        ("legacy.pdf", b"%PDF-1.7", 422, None),
        ("blank.md", b"\n \n", 422, "COMPOSER_LIBRARY_NO_TEXT"),
        ("binary.txt", b"\xff\xfe", 422, "COMPOSER_LIBRARY_INVALID_TEXT"),
        ("large.md", b"x" * 40_001, 413, None),
        ("broken.docx", b"PK not a package", 422, "COMPOSER_LIBRARY_INVALID_PACKAGE"),
    ):
        rejected = _upload(harness, filename, content)
        assert rejected.status_code == status, filename
        if code:
            assert rejected.json()["error"]["code"] == code
    assert len(client.get(_LIBRARY).json()["documents"]) == 2
    for query in ("", "a", "  ", " ".join(f"term{index}" for index in range(9))):
        response = client.post(
            f"{_LIBRARY}/search",
            json={"query": query or " "},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert response.status_code == 422


def test_office_sources_are_indexed_and_rejected_sources_never_persist(
    harness: LibraryHarness,
) -> None:
    exercise_office_and_rejected_sources(harness)


def _pending_versions(harness: LibraryHarness) -> int:
    with Session(harness.engine) as database:
        return len(
            database.scalars(
                select(ComposerLibraryVersionRow).where(
                    ComposerLibraryVersionRow.publication_state == "pending"
                )
            ).all()
        )


def exercise_concurrent_replacements(harness: LibraryHarness) -> None:
    """Two replacements of one revision publish exactly one version."""

    created = _upload(harness, "findings.md", _NOTES.encode())
    assert created.status_code == 201
    document = created.json()
    path = f"{_LIBRARY}/{document['id']}"
    barrier = Barrier(2)

    def replace(index: int) -> int:
        barrier.wait()
        return _upload(
            harness,
            "findings.md",
            f"# Replacement {index}\n\nRevision text {index}.\n".encode(),
            path=f"{path}/versions",
            etag=document["etag"],
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(replace, range(2)))
    assert statuses == [201, 412]
    versions = harness.client.get(f"{path}/versions").json()["versions"]
    assert [item["number"] for item in versions] == [1, 2]
    assert harness.client.get(path).json()["version"] == 2
    assert _pending_versions(harness) == 0


def test_concurrent_replacements_publish_one_version(harness: LibraryHarness) -> None:
    exercise_concurrent_replacements(harness)


def exercise_publication_failures_and_recovery(
    harness: LibraryHarness, mocker: MockerFixture
) -> None:
    """Failed writes publish nothing; interrupted publications are recovered."""

    mocker.patch.object(
        harness.objects, "put", side_effect=ObjectStoreError("unavailable")
    )
    failed = _upload(harness, "findings.md", _NOTES.encode())
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "COMPOSER_LIBRARY_UNAVAILABLE"
    mocker.stopall()
    assert harness.client.get(_LIBRARY).json()["documents"] == []
    assert _pending_versions(harness) == 0

    source = LibrarySource(
        _NOTES.encode(), "text/markdown", "test:library", (_NOTES.strip(),)
    )
    for method, title in (("_finalize", "Interrupted"), ("_write", "Lost")):
        mocker.patch.object(harness.library, method, side_effect=RuntimeError("crash"))
        with pytest.raises(RuntimeError):
            harness.library.create(harness.owner.id, title, source)
        mocker.stopall()
    assert _pending_versions(harness) == 2
    assert harness.client.get(_LIBRARY).json()["documents"] == []

    early = SqlComposerLibraryRepository(
        harness.engine,
        harness.objects,
        publication_lease=timedelta(seconds=30),
        maximum_scanned_chunks=100,
    )
    assert early.recover_pending(limit=10) == 0
    later = SqlComposerLibraryRepository(
        harness.engine,
        harness.objects,
        publication_lease=timedelta(seconds=30),
        maximum_scanned_chunks=100,
        clock=lambda: datetime.now(UTC) + timedelta(hours=1),
    )
    assert later.recover_pending(limit=10) == 2
    assert _pending_versions(harness) == 0
    titles = [
        item["title"] for item in harness.client.get(_LIBRARY).json()["documents"]
    ]
    assert titles == ["Interrupted"]
    recovered = harness.client.get(_LIBRARY).json()["documents"][0]
    content = harness.client.get(
        f"{_LIBRARY}/{recovered['id']}/versions/{recovered['current_version_id']}/content"
    )
    assert content.content == _NOTES.encode()


def test_publication_failures_never_publish_and_recovery_completes(
    harness: LibraryHarness, mocker: MockerFixture
) -> None:
    exercise_publication_failures_and_recovery(harness, mocker)


def exercise_request_validation(harness: LibraryHarness) -> None:
    """Malformed uploads, preconditions and citations are refused before storage."""

    client = harness.client
    invalid = "COMPOSER_REQUEST_INVALID"
    for filename, content, title, code in (
        ("dir\\notes.md", b"# Notes\n", None, invalid),
        ("notes.md", b"", None, invalid),
        ("notes.md", b"# Notes\n", "   ", invalid),
        ("notes.md", b"# Notes\n", "x" * 201, "REQUEST_INVALID"),
    ):
        rejected = _upload(harness, filename, content, title=title)
        assert rejected.status_code == 422, filename
        assert rejected.json()["error"]["code"] == code, filename
    created = _upload(harness, "notes.md", b"# Notes\n").json()
    missing = client.delete(
        f"{_LIBRARY}/{created['id']}", headers={"X-CSRF-Token": "csrf"}
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "COMPOSER_PRECONDITION_REQUIRED"
    reference = {"version_id": created["current_version_id"], "chunk": 0}
    duplicated = client.post(
        f"{_LIBRARY}/excerpts",
        json={"references": [reference, reference]},
        headers={"X-CSRF-Token": "csrf"},
    )
    assert duplicated.status_code == 422
    assert [item["title"] for item in client.get(_LIBRARY).json()["documents"]] == [
        "notes"
    ]


def test_requests_are_validated_before_storage(harness: LibraryHarness) -> None:
    exercise_request_validation(harness)


def _cleanup_rows(harness: LibraryHarness) -> list[str]:
    with Session(harness.engine) as database:
        return list(
            database.scalars(
                select(ComposerLibraryCleanupRow.version_id).where(
                    ComposerLibraryCleanupRow.object_owner_id == str(harness.owner.id)
                )
            )
        )


def _object_exists(harness: LibraryHarness, version_id: str) -> bool:
    try:
        harness.objects.get(
            ObjectKey(ObjectScope.COMPOSER_LIBRARY, harness.owner.id, UUID(version_id))
        )
    except ObjectNotFoundError:
        return False
    return True


def _later(harness: LibraryHarness) -> SqlComposerLibraryRepository:
    """A restarted repository whose clock is past every publication lease."""

    return SqlComposerLibraryRepository(
        harness.engine,
        harness.objects,
        publication_lease=timedelta(seconds=30),
        maximum_scanned_chunks=100,
        clock=lambda: datetime.now(UTC) + timedelta(hours=1),
    )


def exercise_durable_deletion(harness: LibraryHarness, mocker: MockerFixture) -> None:
    """Deleted sources are always removed: after failures, crashes, and races."""

    created = _upload(harness, "notes.md", _NOTES.encode()).json()
    path = f"{_LIBRARY}/{created['id']}"
    replaced = _upload(
        harness, "notes.md", b"# Two\n", path=f"{path}/versions", etag=created["etag"]
    ).json()
    versions = [created["current_version_id"], replaced["current_version_id"]]
    mocker.patch.object(
        harness.objects, "delete", side_effect=ObjectStoreError("unavailable")
    )
    deleted = harness.client.delete(
        path, headers={"X-CSRF-Token": "csrf", "If-Match": replaced["etag"]}
    )
    assert deleted.status_code == 204
    mocker.stopall()
    assert sorted(_cleanup_rows(harness)) == sorted(versions)
    assert all(_object_exists(harness, version) for version in versions)
    assert harness.library.cleanup(limit=10) == 2
    assert _cleanup_rows(harness) == []
    assert not any(_object_exists(harness, version) for version in versions)

    crashed = _upload(harness, "crash.md", _NOTES.encode()).json()
    mocker.patch.object(
        harness.library, "sweep_deleted", side_effect=RuntimeError("crash")
    )
    with pytest.raises(RuntimeError):
        harness.library.delete(
            harness.owner.id, UUID(crashed["id"]), if_match=crashed["etag"]
        )
    mocker.stopall()
    assert harness.client.get(f"{_LIBRARY}/{crashed['id']}").status_code == 404
    assert _cleanup_rows(harness) == [crashed["current_version_id"]]
    assert _object_exists(harness, crashed["current_version_id"])
    assert _later(harness).cleanup(limit=10) == 1
    assert not _object_exists(harness, crashed["current_version_id"])

    racing = _upload(harness, "race.md", _NOTES.encode()).json()
    write = harness.library._write

    def delete_then_write(key: ObjectKey, content: bytes) -> None:
        harness.library.delete(
            harness.owner.id, UUID(racing["id"]), if_match=racing["etag"]
        )
        write(key, content)

    mocker.patch.object(harness.library, "_write", side_effect=delete_then_write)
    source = LibrarySource(b"# Late\n", "text/markdown", "test:library", ("# Late",))
    with pytest.raises(LibraryConflictError):
        harness.library.replace(
            harness.owner.id, UUID(racing["id"]), source, if_match=racing["etag"]
        )
    mocker.stopall()
    pending = [
        version
        for version in _cleanup_rows(harness)
        if version != racing["current_version_id"]
    ]
    assert len(pending) == 1
    assert not _object_exists(harness, pending[0])
    _later(harness).cleanup(limit=10)
    assert _cleanup_rows(harness) == []
    assert not _object_exists(harness, racing["current_version_id"])


def test_deleted_sources_are_eventually_removed(
    harness: LibraryHarness, mocker: MockerFixture
) -> None:
    exercise_durable_deletion(harness, mocker)


def exercise_unicode_search(harness: LibraryHarness) -> None:
    """SQL prefiltering and ranking fold Unicode identically in both profiles."""

    created = _upload(
        harness, "unicode.md", "# Straße ÉTÉ\n\nﬁle naïve".encode(), title="Été Straße"
    )
    assert created.status_code == 201, created.text
    for query in ("Straße", "STRASSE", "été", "ÉTÉ", "file", "NAÏVE"):
        assert [hit["title"] for hit in _search(harness, query)] == ["Été Straße"], (
            query
        )
    for title_filter in ("été", "STRASSE"):
        listed = harness.client.get(_LIBRARY, params={"query": title_filter})
        assert [item["title"] for item in listed.json()["documents"]] == [
            "Été Straße"
        ], title_filter


def test_unicode_search_matches_folded_text(harness: LibraryHarness) -> None:
    exercise_unicode_search(harness)
