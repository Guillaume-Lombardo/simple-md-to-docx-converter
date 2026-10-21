"""Permissioned, cited Composer reference library routes."""

from pathlib import PurePath
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool

from markweave.auth.models import User
from markweave.composer.library import (
    LIBRARY_MEDIA_TYPES,
    chunk_paragraphs,
    extract_paragraphs,
    query_terms,
)
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerRequestTooLargeError,
    ComposerUnavailableError,
)
from markweave.http.composer_library_schemas import (
    LibraryDocumentListResponse,
    LibraryDocumentResponse,
    LibraryExcerptListResponse,
    LibraryExcerptRequest,
    LibraryExcerptResponse,
    LibrarySearchHitResponse,
    LibrarySearchRequest,
    LibrarySearchResponse,
    LibraryVersionListResponse,
    LibraryVersionResponse,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.persistence.composer.library import (
    LibraryDocument,
    LibrarySource,
    LibraryVersion,
    SqlComposerLibraryRepository,
)

from .composer_fill_templates import typed_docx_limits

_MAXIMUM_OFFSET = 2_147_483_647
_FIRST_PRINTABLE_CODEPOINT = 32
_BASE = "/api/v1/composer/library"


def _library(dependencies: HttpDependencies) -> SqlComposerLibraryRepository:
    library = dependencies.components.composer_library
    if library is None:
        raise ComposerUnavailableError
    return library


def _document(record: LibraryDocument, actor_id: UUID) -> LibraryDocumentResponse:
    return LibraryDocumentResponse(
        id=record.id,
        owner_id=record.owner_id,
        title=record.title,
        version=record.version,
        etag=record.etag,
        current_version_id=record.current_version_id,
        shared_with=record.shared_with if actor_id == record.owner_id else (),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _version(record: LibraryVersion) -> LibraryVersionResponse:
    return LibraryVersionResponse(
        id=record.id,
        document_id=record.document_id,
        number=record.number,
        sha256=record.sha256,
        size=record.size,
        media_type=record.media_type,
        chunk_count=record.chunk_count,
        created_at=record.created_at,
    )


def _media_type(filename: str | None) -> str:
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < _FIRST_PRINTABLE_CODEPOINT for character in filename)
    ):
        raise ComposerRequestError
    media_type = LIBRARY_MEDIA_TYPES.get(PurePath(filename).suffix.lower())
    if media_type is None:
        raise ComposerRequestError("Unsupported library source type")
    return media_type


async def _source(dependencies: HttpDependencies, upload: UploadFile) -> LibrarySource:
    """Bound, scan, then parse an upload; nothing is persisted before the scan."""

    maximum = dependencies.settings.composer_upload_max_bytes
    if maximum is None:
        await upload.close()
        raise ComposerUnavailableError
    try:
        data = await upload.read(maximum + 1)
    finally:
        await upload.close()
    if len(data) > maximum:
        raise ComposerRequestTooLargeError
    if not data:
        raise ComposerRequestError
    media_type = _media_type(upload.filename)
    await run_in_threadpool(dependencies.components.scanner.scan, data)
    limits = typed_docx_limits(dependencies)
    paragraphs = await run_in_threadpool(extract_paragraphs, data, media_type, limits)
    chunks = chunk_paragraphs(paragraphs, maximum_chunks=limits.max_xml_elements)
    receipt = f"{dependencies.settings.malware_scanning_mode.value}:library"
    return LibrarySource(data, media_type, receipt, chunks)


def _etag(if_match: str | None) -> str:
    if if_match is None:
        raise ComposerPreconditionRequiredError
    return if_match


def build_router(dependencies: HttpDependencies) -> APIRouter:  # noqa: PLR0915 - explicit library routes
    """Expose owner-scoped library management, bounded search and excerpts."""

    router = APIRouter()

    @router.get(
        _BASE,
        response_model=LibraryDocumentListResponse,
        tags=["composer"],
        responses=error_responses(401, 422, 503),
    )
    def list_library(
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
        query: Annotated[str | None, Query(max_length=200)] = None,
    ) -> LibraryDocumentListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        documents = _library(dependencies).list_visible(
            user.id, limit=limit, offset=offset, query=query
        )
        return LibraryDocumentListResponse(
            documents=tuple(_document(item, user.id) for item in documents),
            limit=limit,
            offset=offset,
        )

    @router.post(
        _BASE,
        response_model=LibraryDocumentResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 413, 422, 503),
    )
    async def create_library_document(
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        file: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form(max_length=200)] = None,
    ) -> LibraryDocumentResponse:
        source = await _source(dependencies, file)
        name = title if title is not None else PurePath(file.filename or "").stem
        try:
            record = await run_in_threadpool(
                _library(dependencies).create, user.id, name, source
            )
        except ValueError:
            raise ComposerRequestError("Library title is invalid") from None
        response.headers["Location"] = f"{_BASE}/{record.id}"
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _document(record, user.id)

    @router.get(
        f"{_BASE}/{{document_id}}",
        response_model=LibraryDocumentResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def get_library_document(
        document_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> LibraryDocumentResponse:
        record = _library(dependencies).get(user.id, document_id)
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _document(record, user.id)

    @router.delete(
        f"{_BASE}/{{document_id}}",
        status_code=204,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 428, 503),
    )
    def delete_library_document(
        document_id: UUID,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> Response:
        _library(dependencies).delete(user.id, document_id, if_match=_etag(if_match))
        return Response(status_code=204, headers={"Cache-Control": "private, no-store"})

    @router.get(
        f"{_BASE}/{{document_id}}/versions",
        response_model=LibraryVersionListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 422, 503),
    )
    def list_library_versions(
        document_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> LibraryVersionListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        versions = _library(dependencies).list_versions(
            user.id, document_id, limit=limit, offset=offset
        )
        return LibraryVersionListResponse(
            versions=tuple(_version(item) for item in versions),
            limit=limit,
            offset=offset,
        )

    @router.post(
        f"{_BASE}/{{document_id}}/versions",
        response_model=LibraryDocumentResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    async def replace_library_document(
        document_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        file: Annotated[UploadFile, File()],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> LibraryDocumentResponse:
        etag = _etag(if_match)
        source = await _source(dependencies, file)
        record = await run_in_threadpool(
            lambda: _library(dependencies).replace(
                user.id, document_id, source, if_match=etag
            )
        )
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _document(record, user.id)

    @router.get(
        f"{_BASE}/{{document_id}}/versions/{{version_id}}/content",
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def download_library_version(
        document_id: UUID,
        version_id: UUID,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> Response:
        version, content = _library(dependencies).download(
            user.id, document_id, version_id
        )
        extension = next(
            suffix
            for suffix, media in LIBRARY_MEDIA_TYPES.items()
            if media == version.media_type
        )
        return Response(
            content=content,
            media_type=version.media_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    f'attachment; filename="library-{version.id}{extension}"'
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.put(
        f"{_BASE}/{{document_id}}/grants/{{user_id}}",
        response_model=LibraryDocumentResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 428, 503),
    )
    def grant_library_document(
        document_id: UUID,
        user_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> LibraryDocumentResponse:
        record = _library(dependencies).grant(
            user.id, document_id, user_id, if_match=_etag(if_match)
        )
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _document(record, user.id)

    @router.delete(
        f"{_BASE}/{{document_id}}/grants/{{user_id}}",
        response_model=LibraryDocumentResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 428, 503),
    )
    def revoke_library_document(
        document_id: UUID,
        user_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> LibraryDocumentResponse:
        record = _library(dependencies).revoke(
            user.id, document_id, user_id, if_match=_etag(if_match)
        )
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _document(record, user.id)

    @router.post(
        f"{_BASE}/search",
        response_model=LibrarySearchResponse,
        tags=["composer"],
        responses=error_responses(401, 422, 503),
    )
    def search_library(
        payload: LibrarySearchRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> LibrarySearchResponse:
        """Search only authorized current versions; the query stays out of URLs."""

        hits = _library(dependencies).search(
            user.id, query_terms(payload.query), limit=payload.limit
        )
        response.headers["Cache-Control"] = "private, no-store"
        return LibrarySearchResponse(
            results=tuple(
                LibrarySearchHitResponse(
                    document_id=hit.document_id,
                    title=hit.title,
                    version_id=hit.version_id,
                    version_number=hit.version_number,
                    chunk=hit.chunk,
                    score=hit.score,
                    snippet=hit.snippet,
                )
                for hit in hits
            )
        )

    @router.post(
        f"{_BASE}/excerpts",
        response_model=LibraryExcerptListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 422, 503),
    )
    def read_library_excerpts(
        payload: LibraryExcerptRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> LibraryExcerptListResponse:
        """Return exact cited chunks while the caller is still authorized."""

        references = tuple((item.version_id, item.chunk) for item in payload.references)
        if len(set(references)) != len(references):
            raise ComposerRequestError("Citation selection is invalid")
        excerpts = _library(dependencies).excerpts(user.id, references)
        response.headers["Cache-Control"] = "private, no-store"
        return LibraryExcerptListResponse(
            excerpts=tuple(
                LibraryExcerptResponse(
                    document_id=item.document_id,
                    title=item.title,
                    version_id=item.version_id,
                    version_number=item.version_number,
                    version_sha256=item.version_sha256,
                    chunk=item.chunk,
                    text=item.text,
                )
                for item in excerpts
            )
        )

    return router
