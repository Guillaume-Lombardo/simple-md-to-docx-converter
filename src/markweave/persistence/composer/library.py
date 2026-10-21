"""Owner-scoped cited library documents with two-phase version publication.

Access is the owner or an explicitly granted active user. Every read path,
search and citation check filters that authorization in SQL before any text is
loaded, so a search hit or a citation never grants access to its document.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, Engine, delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.library import ScoredChunk, normalize_search, score_chunk
from markweave.persistence.composer.audit import record_content_audit
from markweave.persistence.composer.common import utc, validate_page
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerLibraryChunkRow,
    ComposerLibraryCleanupRow,
    ComposerLibraryDocumentRow,
    ComposerLibraryGrantRow,
    ComposerLibraryVersionRow,
    UserRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)

_MAXIMUM_TITLE_CHARACTERS = 200


class LibraryNotFoundError(LookupError):
    """A library document, version, or excerpt is absent or unauthorized."""


class LibraryConflictError(RuntimeError):
    """The library document revision or grant state changed."""


class LibraryArtifactError(RuntimeError):
    """The exact library source object is absent, corrupt, or unavailable."""


@dataclass(frozen=True, slots=True)
class LibraryDocument:
    id: UUID
    owner_id: UUID
    title: str
    version: int
    current_version_id: UUID
    shared_with: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime

    @property
    def etag(self) -> str:
        return f'"{self.version}"'


@dataclass(frozen=True, slots=True)
class LibraryVersion:
    id: UUID
    document_id: UUID
    number: int
    sha256: str
    size: int
    media_type: str
    chunk_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LibraryExcerpt:
    """One authorized chunk of an exact version, citable by its identity."""

    document_id: UUID
    title: str
    version_id: UUID
    version_number: int
    version_sha256: str
    chunk: int
    text: str


@dataclass(frozen=True, slots=True)
class LibrarySearchHit:
    document_id: UUID
    title: str
    version_id: UUID
    version_number: int
    chunk: int
    score: int
    snippet: str


@dataclass(frozen=True, slots=True)
class LibrarySource:
    """Caller-validated, scanned bytes and their bounded citable chunks."""

    content: bytes
    media_type: str
    scan_receipt: str
    chunks: tuple[str, ...]


def _visible(actor_id: UUID) -> ColumnElement[bool]:
    return or_(
        ComposerLibraryDocumentRow.owner_id == str(actor_id),
        exists(
            select(ComposerLibraryGrantRow.document_id).where(
                ComposerLibraryGrantRow.document_id == ComposerLibraryDocumentRow.id,
                ComposerLibraryGrantRow.user_id == str(actor_id),
            )
        ),
    )


def _active(actor_id: UUID) -> ColumnElement[bool]:
    return exists(
        select(UserRow.id).where(UserRow.id == str(actor_id), UserRow.active.is_(True))
    )


def _version(row: ComposerLibraryVersionRow) -> LibraryVersion:
    return LibraryVersion(
        UUID(row.id),
        UUID(row.document_id),
        row.number,
        row.sha256,
        row.size,
        row.media_type,
        row.chunk_count,
        utc(row.created_at),
    )


def _identity(database: Session, row: ComposerLibraryDocumentRow) -> LibraryDocument:
    if row.current_version_id is None:
        raise LibraryNotFoundError("Library document does not exist")
    grants = tuple(
        UUID(user_id)
        for user_id in database.scalars(
            select(ComposerLibraryGrantRow.user_id)
            .where(ComposerLibraryGrantRow.document_id == row.id)
            .order_by(ComposerLibraryGrantRow.user_id)
        )
    )
    return LibraryDocument(
        UUID(row.id),
        UUID(row.owner_id),
        row.title,
        row.revision,
        UUID(row.current_version_id),
        grants,
        utc(row.created_at),
        utc(row.updated_at),
    )


def require_excerpt_access(
    database: Session,
    actor_id: UUID,
    refs: Iterable[tuple[UUID, int]],
    *,
    for_update: bool = False,
) -> tuple[LibraryExcerpt, ...]:
    """Recheck each cited exact version and chunk for the actor in a transaction.

    With ``for_update`` the cited documents stay locked until commit, so a
    concurrent revocation or deletion cannot interleave with the caller.
    """

    excerpts: list[LibraryExcerpt] = []
    for version_id, ordinal in refs:
        statement = (
            select(
                ComposerLibraryChunkRow,
                ComposerLibraryVersionRow,
                ComposerLibraryDocumentRow,
            )
            .join(
                ComposerLibraryVersionRow,
                ComposerLibraryChunkRow.version_id == ComposerLibraryVersionRow.id,
            )
            .join(
                ComposerLibraryDocumentRow,
                ComposerLibraryVersionRow.document_id == ComposerLibraryDocumentRow.id,
            )
            .where(
                ComposerLibraryChunkRow.version_id == str(version_id),
                ComposerLibraryChunkRow.ordinal == ordinal,
                ComposerLibraryVersionRow.publication_state == "published",
                ComposerLibraryDocumentRow.publication_state == "published",
                _visible(actor_id),
                _active(actor_id),
            )
        )
        if for_update:
            statement = statement.with_for_update(of=ComposerLibraryDocumentRow)
        row = database.execute(statement).one_or_none()
        if row is None:
            raise LibraryNotFoundError("Library excerpt is unavailable")
        chunk, version, document = row
        excerpts.append(
            LibraryExcerpt(
                UUID(document.id),
                document.title,
                UUID(version.id),
                version.number,
                version.sha256,
                chunk.ordinal,
                chunk.text,
            )
        )
    return tuple(excerpts)


class SqlComposerLibraryRepository:
    """Equivalent SQLite/filesystem and PostgreSQL/S3 library persistence."""

    def __init__(  # noqa: PLR0913 - explicit storage and bounded limits
        self,
        engine: Engine,
        objects: ObjectStore,
        *,
        publication_lease: timedelta,
        maximum_scanned_chunks: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        if publication_lease <= timedelta(0) or maximum_scanned_chunks <= 0:
            raise ValueError("Library repository limits must be positive")
        self._engine, self._objects = engine, objects
        self._clock, self._new_id = clock, new_id
        self._publication_lease = publication_lease
        self._maximum_scanned = maximum_scanned_chunks

    def create(
        self, owner_id: UUID, title: str, source: LibrarySource
    ) -> LibraryDocument:
        """Publish a scanned, parsed source as a new private library document."""

        clean_title = self._title(title)
        self._validate(source)
        document_id, version_id, token = self._new_id(), self._new_id(), self._new_id()
        now = self._clock()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                if (
                    database.scalar(select(UserRow.id).where(_active_user(owner_id)))
                    is None
                ):
                    raise LibraryNotFoundError("Library owner does not exist")
                database.add(
                    ComposerLibraryDocumentRow(
                        id=str(document_id),
                        owner_id=str(owner_id),
                        title=clean_title,
                        search_title=normalize_search(clean_title),
                        revision=1,
                        current_version_id=None,
                        publication_state="pending",
                        created_at=now,
                        updated_at=now,
                    )
                )
                database.flush()
                self._reserve(
                    database,
                    document_id,
                    version_id,
                    owner_id,
                    owner_id,
                    source,
                    1,
                    1,
                    token,
                    now,
                )
            return self._publish(document_id, version_id, owner_id, token, source)
        except LibraryNotFoundError, LibraryConflictError, LibraryArtifactError:
            raise
        except IntegrityError:
            raise LibraryConflictError("Library document already exists") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def replace(
        self,
        actor_id: UUID,
        document_id: UUID,
        source: LibrarySource,
        *,
        if_match: str,
    ) -> LibraryDocument:
        """Publish an owner's next immutable version after a verified object write."""

        self._validate(source)
        version_id, token, now = self._new_id(), self._new_id(), self._clock()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._mutable(database, actor_id, document_id, if_match)
                if database.scalar(
                    select(ComposerLibraryVersionRow.id).where(
                        ComposerLibraryVersionRow.document_id == str(document_id),
                        ComposerLibraryVersionRow.publication_state == "pending",
                        ComposerLibraryVersionRow.lease_expires_at > now,
                    )
                ):
                    # One live publication per document keeps version numbers
                    # contiguous; an expired one is superseded and later swept.
                    raise LibraryConflictError("Library document is being replaced")
                number = (
                    int(
                        database.scalar(
                            select(
                                func.coalesce(
                                    func.max(ComposerLibraryVersionRow.number), 0
                                )
                            ).where(
                                ComposerLibraryVersionRow.document_id
                                == str(document_id)
                            )
                        )
                        or 0
                    )
                    + 1
                )
                owner_id = UUID(row.owner_id)
                self._reserve(
                    database,
                    document_id,
                    version_id,
                    owner_id,
                    actor_id,
                    source,
                    number,
                    row.revision,
                    token,
                    now,
                )
            return self._publish(document_id, version_id, owner_id, token, source)
        except LibraryNotFoundError, LibraryConflictError, LibraryArtifactError:
            raise
        except IntegrityError:
            raise LibraryConflictError("Library document version changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get(self, actor_id: UUID, document_id: UUID) -> LibraryDocument:
        try:
            with Session(self._engine) as database:
                return _identity(
                    database, self._readable(database, actor_id, document_id)
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_visible(
        self,
        actor_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        query: str | None = None,
    ) -> tuple[LibraryDocument, ...]:
        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                statement = select(ComposerLibraryDocumentRow).where(
                    ComposerLibraryDocumentRow.publication_state == "published",
                    _visible(actor_id),
                    _active(actor_id),
                )
                if query:
                    statement = statement.where(
                        ComposerLibraryDocumentRow.search_title.contains(
                            normalize_search(query), autoescape=True
                        )
                    )
                rows = database.scalars(
                    statement.order_by(
                        ComposerLibraryDocumentRow.title, ComposerLibraryDocumentRow.id
                    )
                    .limit(limit)
                    .offset(offset)
                ).all()
                return tuple(_identity(database, row) for row in rows)
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_versions(
        self, actor_id: UUID, document_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[LibraryVersion, ...]:
        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                self._readable(database, actor_id, document_id)
                return tuple(
                    _version(row)
                    for row in database.scalars(
                        select(ComposerLibraryVersionRow)
                        .where(
                            ComposerLibraryVersionRow.document_id == str(document_id),
                            ComposerLibraryVersionRow.publication_state == "published",
                        )
                        .order_by(ComposerLibraryVersionRow.number)
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def download(
        self, actor_id: UUID, document_id: UUID, version_id: UUID
    ) -> tuple[LibraryVersion, bytes]:
        """Read exact published bytes and verify the recorded digest and length."""

        try:
            with Session(self._engine) as database:
                self._readable(database, actor_id, document_id)
                row = database.scalar(
                    select(ComposerLibraryVersionRow).where(
                        ComposerLibraryVersionRow.id == str(version_id),
                        ComposerLibraryVersionRow.document_id == str(document_id),
                        ComposerLibraryVersionRow.publication_state == "published",
                    )
                )
                if row is None:
                    raise LibraryNotFoundError("Library version does not exist")
                version = _version(row)
                key = ObjectKey(
                    ObjectScope.COMPOSER_LIBRARY, UUID(row.object_owner_id), version_id
                )
            content = self._objects.get(key)
            if (
                len(content) != version.size
                or hashlib.sha256(content).hexdigest() != version.sha256
            ):
                raise LibraryArtifactError("Library content integrity check failed")
            return version, content
        except ObjectNotFoundError, ObjectStoreError:
            raise LibraryArtifactError("Library content is unavailable") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def grant(
        self, actor_id: UUID, document_id: UUID, user_id: UUID, *, if_match: str
    ) -> LibraryDocument:
        return self._change_grant(actor_id, document_id, user_id, if_match, grant=True)

    def revoke(
        self, actor_id: UUID, document_id: UUID, user_id: UUID, *, if_match: str
    ) -> LibraryDocument:
        return self._change_grant(actor_id, document_id, user_id, if_match, grant=False)

    def delete(self, actor_id: UUID, document_id: UUID, *, if_match: str) -> None:
        """Remove an owner's document; later citations report it as unavailable.

        Every version's object, published or still being written, gets a durable
        cleanup record in the same transaction that removes the metadata, so a
        crash or an object-store failure leaves work for the next sweep rather
        than an orphan. A pending upload's record waits for its lease to expire,
        because its object may still be written after this commit.
        """

        now = self._clock()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._mutable(database, actor_id, document_id, if_match)
                versions = database.scalars(
                    select(ComposerLibraryVersionRow)
                    .where(ComposerLibraryVersionRow.document_id == str(document_id))
                    .with_for_update()
                ).all()
                for version in versions:
                    lease = version.lease_expires_at
                    database.add(
                        ComposerLibraryCleanupRow(
                            version_id=version.id,
                            object_owner_id=version.object_owner_id,
                            not_before=(
                                max(utc(now), utc(lease))
                                if version.publication_state == "pending"
                                and lease is not None
                                else now
                            ),
                            created_at=now,
                        )
                    )
                self._audit(database, row, actor_id, "delete")
                database.delete(row)
        except LibraryNotFoundError, LibraryConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None
        with suppress(ObjectStoreError, SQLAlchemyError):
            self.sweep_deleted(limit=max(len(versions), 1))

    def sweep_deleted(self, *, limit: int) -> int:
        """Remove due objects of deleted versions, then their cleanup records."""

        if type(limit) is not int or limit <= 0:
            raise ValueError("Cleanup limit must be positive")
        now = self._clock()
        with Session(self._engine) as database:
            due = tuple(
                (row.version_id, row.object_owner_id)
                for row in database.scalars(
                    select(ComposerLibraryCleanupRow)
                    .where(ComposerLibraryCleanupRow.not_before <= now)
                    .order_by(
                        ComposerLibraryCleanupRow.not_before,
                        ComposerLibraryCleanupRow.version_id,
                    )
                    .limit(limit)
                )
            )
        removed = 0
        for version_id, owner_id in due:
            try:
                self._objects.delete(
                    ObjectKey(
                        ObjectScope.COMPOSER_LIBRARY, UUID(owner_id), UUID(version_id)
                    )
                )
            except ObjectStoreError:
                continue
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                database.execute(
                    delete(ComposerLibraryCleanupRow).where(
                        ComposerLibraryCleanupRow.version_id == version_id
                    )
                )
            removed += 1
        return removed

    def cleanup(self, *, limit: int) -> int:
        """Bounded maintenance: settle expired uploads and sweep deleted objects."""

        return self.recover_pending(limit=limit) + self.sweep_deleted(limit=limit)

    def search(
        self, actor_id: UUID, terms: tuple[str, ...], *, limit: int
    ) -> tuple[LibrarySearchHit, ...]:
        """Rank authorized current-version chunks that contain every term."""

        if not terms or limit <= 0:
            raise ValueError("Library search is invalid")
        try:
            with Session(self._engine) as database:
                rows = database.execute(
                    select(
                        ComposerLibraryChunkRow,
                        ComposerLibraryVersionRow,
                        ComposerLibraryDocumentRow,
                    )
                    .join(
                        ComposerLibraryVersionRow,
                        ComposerLibraryChunkRow.version_id
                        == ComposerLibraryVersionRow.id,
                    )
                    .join(
                        ComposerLibraryDocumentRow,
                        ComposerLibraryDocumentRow.current_version_id
                        == ComposerLibraryVersionRow.id,
                    )
                    .where(
                        ComposerLibraryDocumentRow.publication_state == "published",
                        _visible(actor_id),
                        _active(actor_id),
                        *(
                            ComposerLibraryChunkRow.search_text.contains(
                                term, autoescape=True
                            )
                            for term in terms
                        ),
                    )
                    .order_by(
                        ComposerLibraryDocumentRow.title,
                        ComposerLibraryDocumentRow.id,
                        ComposerLibraryChunkRow.ordinal,
                    )
                    .limit(self._maximum_scanned)
                ).all()
        except SQLAlchemyError:
            raise PersistenceError from None
        hits: list[tuple[ScoredChunk, LibrarySearchHit]] = []
        for chunk, version, document in rows:
            scored = score_chunk(chunk.text, chunk.search_text, terms)
            if scored is None:
                continue
            hits.append(
                (
                    scored,
                    LibrarySearchHit(
                        UUID(document.id),
                        document.title,
                        UUID(version.id),
                        version.number,
                        chunk.ordinal,
                        scored.score,
                        scored.snippet,
                    ),
                )
            )
        hits.sort(key=lambda item: -item[0].score)
        return tuple(hit for _scored, hit in hits[:limit])

    def excerpts(
        self, actor_id: UUID, refs: tuple[tuple[UUID, int], ...]
    ) -> tuple[LibraryExcerpt, ...]:
        """Return exact cited chunks only while each is still authorized."""

        try:
            with Session(self._engine) as database:
                return require_excerpt_access(database, actor_id, refs)
        except SQLAlchemyError:
            raise PersistenceError from None

    def recover_pending(self, *, limit: int) -> int:
        """Finalize complete expired objects or remove abandoned hidden reservations."""

        if type(limit) is not int or limit <= 0:
            raise ValueError("Recovery limit must be positive")
        now = self._clock()
        with Session(self._engine) as database:
            reservations = tuple(
                (
                    UUID(row.document_id),
                    UUID(row.id),
                    UUID(row.publication_token),
                    UUID(row.object_owner_id),
                    row.sha256,
                    row.size,
                )
                for row in database.scalars(
                    select(ComposerLibraryVersionRow)
                    .where(
                        ComposerLibraryVersionRow.publication_state == "pending",
                        ComposerLibraryVersionRow.lease_expires_at <= now,
                    )
                    .order_by(
                        ComposerLibraryVersionRow.created_at,
                        ComposerLibraryVersionRow.id,
                    )
                    .limit(limit)
                )
                if row.publication_token is not None
            )
        processed = 0
        for document_id, version_id, token, owner_id, digest, size in reservations:
            key = ObjectKey(ObjectScope.COMPOSER_LIBRARY, owner_id, version_id)
            try:
                content = self._objects.get(key)
                valid = (
                    len(content) == size
                    and hashlib.sha256(content).hexdigest() == digest
                )
            except ObjectNotFoundError:
                valid = False
            except ObjectStoreError:
                continue
            try:
                if valid:
                    self._finalize(document_id, version_id, token)
                else:
                    self._discard(document_id, version_id, token, owner_id)
            except LibraryConflictError:
                self._discard(document_id, version_id, token, owner_id)
            processed += 1
        return processed

    def _readable(
        self, database: Session, actor_id: UUID, document_id: UUID
    ) -> ComposerLibraryDocumentRow:
        row = database.scalar(
            select(ComposerLibraryDocumentRow).where(
                ComposerLibraryDocumentRow.id == str(document_id),
                ComposerLibraryDocumentRow.publication_state == "published",
                _visible(actor_id),
                _active(actor_id),
            )
        )
        if row is None:
            raise LibraryNotFoundError("Library document does not exist")
        return row

    @staticmethod
    def _mutable(
        database: Session, actor_id: UUID, document_id: UUID, if_match: str
    ) -> ComposerLibraryDocumentRow:
        row = database.scalar(
            select(ComposerLibraryDocumentRow)
            .where(
                ComposerLibraryDocumentRow.id == str(document_id),
                ComposerLibraryDocumentRow.publication_state == "published",
                ComposerLibraryDocumentRow.owner_id == str(actor_id),
                _active(actor_id),
            )
            .with_for_update()
        )
        if row is None:
            raise LibraryNotFoundError("Library document does not exist")
        if if_match != f'"{row.revision}"':
            raise LibraryConflictError("Library document changed")
        return row

    def _change_grant(
        self,
        actor_id: UUID,
        document_id: UUID,
        user_id: UUID,
        if_match: str,
        *,
        grant: bool,
    ) -> LibraryDocument:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._mutable(database, actor_id, document_id, if_match)
                if user_id == UUID(row.owner_id):
                    raise LibraryConflictError("Owner access cannot be changed")
                if (
                    database.scalar(select(UserRow.id).where(_active_user(user_id)))
                    is None
                ):
                    raise LibraryNotFoundError("Target user does not exist")
                existing = database.get(
                    ComposerLibraryGrantRow, (str(document_id), str(user_id))
                )
                if (existing is not None) == grant:
                    raise LibraryConflictError("Library grant state changed")
                if grant:
                    database.add(
                        ComposerLibraryGrantRow(
                            document_id=str(document_id),
                            user_id=str(user_id),
                            created_at=self._clock(),
                        )
                    )
                else:
                    database.delete(existing)
                row.revision += 1
                row.updated_at = self._clock()
                self._audit(database, row, actor_id, "grant" if grant else "revoke")
                database.flush()
                return _identity(database, row)
        except LibraryNotFoundError, LibraryConflictError:
            raise
        except IntegrityError:
            raise LibraryConflictError("Library grant state changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def _reserve(  # noqa: PLR0913, PLR0917 - complete immutable version identity
        self,
        database: Session,
        document_id: UUID,
        version_id: UUID,
        owner_id: UUID,
        actor_id: UUID,
        source: LibrarySource,
        number: int,
        expected_revision: int,
        token: UUID,
        now: datetime,
    ) -> None:
        database.add(
            ComposerLibraryVersionRow(
                id=str(version_id),
                document_id=str(document_id),
                number=number,
                expected_revision=expected_revision,
                object_owner_id=str(owner_id),
                created_by=str(actor_id),
                sha256=hashlib.sha256(source.content).hexdigest(),
                size=len(source.content),
                media_type=source.media_type,
                scan_receipt=source.scan_receipt,
                chunk_count=len(source.chunks),
                publication_state="pending",
                publication_token=str(token),
                lease_expires_at=now + self._publication_lease,
                created_at=now,
            )
        )
        database.flush()
        database.add_all(
            ComposerLibraryChunkRow(
                version_id=str(version_id),
                ordinal=ordinal,
                text=text,
                search_text=normalize_search(text),
            )
            for ordinal, text in enumerate(source.chunks)
        )

    def _publish(
        self,
        document_id: UUID,
        version_id: UUID,
        owner_id: UUID,
        token: UUID,
        source: LibrarySource,
    ) -> LibraryDocument:
        try:
            self._write(
                ObjectKey(ObjectScope.COMPOSER_LIBRARY, owner_id, version_id),
                source.content,
            )
            return self._finalize(document_id, version_id, token)
        except LibraryConflictError, LibraryArtifactError:
            # A losing or failed publication never stays visible; if cleanup
            # itself fails, the expired reservation is swept by recovery.
            with suppress(ObjectStoreError, SQLAlchemyError):
                self._discard(document_id, version_id, token, owner_id)
            raise

    def _write(self, key: ObjectKey, content: bytes) -> None:
        try:
            self._objects.put(key, content)
            if self._objects.get(key) != content:
                raise LibraryArtifactError("Library content integrity check failed")
        except ObjectNotFoundError, ObjectStoreError:
            raise LibraryArtifactError("Library content publication failed") from None

    def _finalize(
        self, document_id: UUID, version_id: UUID, token: UUID
    ) -> LibraryDocument:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                version = database.scalar(
                    select(ComposerLibraryVersionRow)
                    .where(
                        ComposerLibraryVersionRow.id == str(version_id),
                        ComposerLibraryVersionRow.document_id == str(document_id),
                        ComposerLibraryVersionRow.publication_state == "pending",
                        ComposerLibraryVersionRow.publication_token == str(token),
                    )
                    .with_for_update()
                )
                row = (
                    None
                    if version is None
                    else database.scalar(
                        select(ComposerLibraryDocumentRow)
                        .where(
                            ComposerLibraryDocumentRow.id == str(document_id),
                            ComposerLibraryDocumentRow.revision
                            == version.expected_revision,
                        )
                        .with_for_update()
                    )
                )
                if version is None or row is None:
                    raise LibraryConflictError("Library publication changed")
                first = row.current_version_id is None
                if first != (version.number == 1):
                    raise LibraryConflictError("Library publication changed")
                row.current_version_id = str(version_id)
                row.publication_state = "published"
                if not first:
                    row.revision += 1
                row.updated_at = self._clock()
                version.publication_state = "published"
                version.publication_token = None
                version.lease_expires_at = None
                self._audit(
                    database,
                    row,
                    UUID(version.created_by),
                    "create" if first else "replace",
                )
                database.flush()
                return _identity(database, row)
        except LibraryConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def _discard(
        self, document_id: UUID, version_id: UUID, token: UUID, owner_id: UUID
    ) -> None:
        with Session(self._engine) as database, database.begin():
            serialize_sqlite_write(database, self._engine)
            version = database.scalar(
                select(ComposerLibraryVersionRow)
                .where(
                    ComposerLibraryVersionRow.id == str(version_id),
                    ComposerLibraryVersionRow.publication_state == "pending",
                    ComposerLibraryVersionRow.publication_token == str(token),
                )
                .with_for_update()
            )
            if version is None:
                if database.get(ComposerLibraryVersionRow, str(version_id)) is None:
                    # The document was deleted while this upload was in flight:
                    # nothing references the object, and its cleanup record
                    # covers a crash before this best-effort removal.
                    with suppress(ObjectStoreError):
                        self._objects.delete(
                            ObjectKey(
                                ObjectScope.COMPOSER_LIBRARY, owner_id, version_id
                            )
                        )
                return
            # Keep the reservation locked until object removal succeeds. A crash
            # leaves a hidden row for the next recovery sweep, never an orphan.
            self._objects.delete(
                ObjectKey(
                    ObjectScope.COMPOSER_LIBRARY,
                    UUID(version.object_owner_id),
                    version_id,
                )
            )
            database.delete(version)
            row = database.get(ComposerLibraryDocumentRow, str(document_id))
            if row is not None and row.current_version_id is None:
                database.delete(row)

    def _audit(
        self,
        database: Session,
        row: ComposerLibraryDocumentRow,
        actor_id: UUID,
        operation: str,
    ) -> None:
        record_content_audit(
            database,
            event_id=self._new_id(),
            owner_id=UUID(row.owner_id),
            actor_id=actor_id,
            operation=f"library_{operation}",
            target_kind="library_document",
            target_id=UUID(row.id),
            draft_id=None,
            draft_version=row.revision,
            created_at=self._clock(),
        )

    @staticmethod
    def _title(title: str) -> str:
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title.strip()) > _MAXIMUM_TITLE_CHARACTERS
        ):
            raise ValueError("Library title is invalid")
        return title.strip()

    @staticmethod
    def _validate(source: LibrarySource) -> None:
        if (
            not isinstance(source.content, bytes)
            or not source.content
            or not source.scan_receipt.strip()
            or not source.media_type
            or not all(isinstance(chunk, str) and chunk for chunk in source.chunks)
        ):
            raise ValueError("Library source is invalid")


def _active_user(user_id: UUID) -> ColumnElement[bool]:
    return (UserRow.id == str(user_id)) & UserRow.active.is_(True)
