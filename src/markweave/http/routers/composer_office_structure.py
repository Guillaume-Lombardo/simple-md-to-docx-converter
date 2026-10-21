"""Read and apply bounded structural DOCX and PPTX edits on exact revisions."""

import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response

from markweave.auth.models import User
from markweave.composer.docx_edits import DOCX_EDIT_MEDIA_TYPE, DOCX_PACKAGE
from markweave.composer.office_edits import OFFICE_EDIT_ENGINE_VERSION, PackageKind
from markweave.composer.office_structure import (
    MoveSlide,
    ReplaceImage,
    SetParagraphStyle,
    StructureOperation,
    apply_structure_edits,
    inspect_structure,
    prepare_image,
    structure_record,
)
from markweave.composer.pptx_edits import PPTX_EDIT_MEDIA_TYPE, PPTX_PACKAGE
from markweave.composer.typed_templates import TypedDocxLimits
from markweave.conversion.images import ImageLimits
from markweave.http.composer_errors import (
    ComposerRequestError,
    ComposerRequestTooLargeError,
    ComposerUnavailableError,
)
from markweave.http.composer_office_structure_schemas import (
    ComposerImageTargetResponse,
    ComposerMoveSlideOperation,
    ComposerOfficeStructureEditRequest,
    ComposerOfficeStructureResponse,
    ComposerParagraphStyleResponse,
    ComposerParagraphTargetResponse,
    ComposerReplaceImageOperation,
    ComposerSlideTargetResponse,
    ComposerStructureOperation,
)
from markweave.http.composer_schemas import ComposerRevisionResponse
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses

from .composer_docx_edits import (
    _store,
    office_download,
    publish_office_edit,
    published_response,
)
from .composer_fill_templates import typed_docx_limits
from .composer_revisions import _require_headers

_REVISION = "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}"
_FORMATS: dict[str, tuple[str, PackageKind]] = {
    "docx": (DOCX_EDIT_MEDIA_TYPE, DOCX_PACKAGE),
    "pptx": (PPTX_EDIT_MEDIA_TYPE, PPTX_PACKAGE),
}


def _image_limits(dependencies: HttpDependencies) -> ImageLimits:
    settings = dependencies.settings
    return ImageLimits(
        settings.conversion_image_max_source_bytes,
        settings.conversion_image_max_width_pixels,
        settings.conversion_image_max_height_pixels,
        settings.conversion_image_max_pixels,
        settings.conversion_image_max_svg_elements,
        settings.conversion_image_max_svg_depth,
    )


def _image_bytes(dependencies: HttpDependencies, encoded: str) -> bytes:
    maximum = dependencies.settings.composer_upload_max_bytes
    if maximum is None:
        raise ComposerUnavailableError
    if len(encoded) > (maximum + 2) // 3 * 4:
        raise ComposerRequestTooLargeError
    try:
        data = base64.b64decode(encoded, validate=True)
    except binascii.Error, ValueError:
        raise ComposerRequestError("Replacement image encoding is invalid") from None
    if not data or len(data) > maximum:
        raise ComposerRequestError("Replacement image is invalid")
    return data


def _operations(
    dependencies: HttpDependencies,
    requested: tuple[ComposerStructureOperation, ...],
    content: bytes,
    limits: TypedDocxLimits,
    kind: PackageKind,
) -> tuple[StructureOperation, ...]:
    """Translate requests; scan every image before it is parsed or re-encoded."""

    images = {
        item.target: item.media_type
        for item in inspect_structure(content, limits, kind).images
    }
    operations: list[StructureOperation] = []
    for item in requested:
        if isinstance(item, ComposerMoveSlideOperation):
            operations.append(
                MoveSlide(item.slide_id, item.expected_position, item.position)
            )
        elif isinstance(item, ComposerReplaceImageOperation):
            data = _image_bytes(dependencies, item.content_base64)
            dependencies.components.scanner.scan(data)
            media_type = images.get(item.target)
            if media_type is None:
                raise kind.error("unsupported_target", (item.target,))
            try:
                prepared = prepare_image(
                    data, item.filename, media_type, _image_limits(dependencies)
                )
            except ValueError:
                raise kind.error("invalid_image", (item.target,)) from None
            operations.append(ReplaceImage(item.target, item.expected_sha256, prepared))
        else:
            operations.append(
                SetParagraphStyle(item.target, item.expected_style, item.style)
            )
    return tuple(operations)


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Expose the qualified structural families: styles, slide order, and images."""

    router = APIRouter()

    for name, (media_type, kind) in _FORMATS.items():
        _register(router, dependencies, name, media_type, kind)
    return router


def _register(
    router: APIRouter,
    dependencies: HttpDependencies,
    name: str,
    media_type: str,
    kind: PackageKind,
) -> None:
    @router.get(
        f"{_REVISION}/{name}-structure",
        response_model=ComposerOfficeStructureResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 413, 422, 503),
        name=f"get_{name}_structure",
    )
    def read_structure(
        draft_id: UUID,
        revision_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerOfficeStructureResponse:
        """List the paragraph styles, slides, and images a reviewer may change."""

        store = _store(dependencies)
        revision = store.get_revision(user.id, draft_id, revision_id)
        content, digest = office_download(store, user.id, revision, media_type)
        structure = inspect_structure(content, typed_docx_limits(dependencies), kind)
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerOfficeStructureResponse(
            format="pptx" if name == "pptx" else "docx",
            revision_id=revision.id,
            download_sha256=digest,
            engine_version=OFFICE_EDIT_ENGINE_VERSION,
            paragraphs=tuple(
                ComposerParagraphTargetResponse(
                    target=item.target,
                    paragraph=item.paragraph,
                    in_table=item.in_table,
                    style=item.style,
                    text=item.text,
                )
                for item in structure.paragraphs
            ),
            paragraph_styles=tuple(
                ComposerParagraphStyleResponse(style_id=item.style_id, name=item.name)
                for item in structure.paragraph_styles
            ),
            slides=tuple(
                ComposerSlideTargetResponse(
                    slide_id=item.slide_id, position=item.position, title=item.title
                )
                for item in structure.slides
            ),
            images=tuple(
                ComposerImageTargetResponse(
                    target=item.target,
                    media_type=item.media_type,
                    sha256=item.sha256,
                    size=item.size,
                    used_by=item.used_by,
                )
                for item in structure.images
            ),
        )

    @router.post(
        f"{_REVISION}/{name}-structure-edits",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
        name=f"edit_{name}_structure",
    )
    def edit_structure(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        revision_id: UUID,
        payload: ComposerOfficeStructureEditRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        """Publish a new revision that differs only in the requested structure."""

        headers = _require_headers(if_match, idempotency_key)
        store = _store(dependencies)
        limits = typed_docx_limits(dependencies)
        base = store.get_revision(user.id, draft_id, revision_id)
        content, _digest = office_download(store, user.id, base, media_type)
        operations = _operations(
            dependencies, payload.operations, content, limits, kind
        )
        revision = publish_office_edit(
            dependencies,
            user,
            draft_id,
            revision_id,
            headers,
            label=f"{name}_structure_edit",
            media_type=media_type,
            record=structure_record(operations),
            edit=lambda source, limits: apply_structure_edits(
                source, operations, limits, kind
            ),
        )
        return published_response(dependencies, user, draft_id, revision, response)
