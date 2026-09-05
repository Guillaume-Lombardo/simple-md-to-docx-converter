"""Reverse-conversion capability routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from markweave.auth.models import User
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.http.schemas import ReversionCapabilitiesResponse
from markweave.reversions.capabilities import build_reversion_capabilities


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Build reverse-conversion routes bound to one application."""

    router = APIRouter()

    @router.get(
        "/api/v1/reversions/capabilities",
        response_model=ReversionCapabilitiesResponse,
        tags=["reversions"],
        responses=error_responses(401, 503),
    )
    def get_reversion_capabilities(
        response: Response,
        _actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> ReversionCapabilitiesResponse:
        capabilities = build_reversion_capabilities(
            dependencies.settings.reversion_upload_max_bytes
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        policy = capabilities.admission
        return ReversionCapabilitiesResponse(
            schema_version=capabilities.schema_version,
            format_families=tuple(
                {
                    "family": approved.family,
                    "extensions": approved.extensions,
                    "detected_formats": approved.detected_formats,
                    "content_detection": approved.content_detection,
                    "selected_parser_format": approved.selected_parser_format,
                }
                for approved in policy.formats
            ),
            admission={
                "extension_is_hint": policy.extension_is_hint,
                "mismatch_policy": policy.mismatch_policy,
                "undetected_policy": policy.undetected_policy,
                "csv_policy": policy.csv_policy,
                "scanner_order": policy.scanner_order,
            },
            maximum_upload_bytes=capabilities.maximum_upload_bytes,
            result_package_modes=capabilities.result_package_modes,
            pdf={
                "contract": capabilities.pdf.contract,
                "document_model_available": (capabilities.pdf.document_model_available),
                "embedded_assets_available": (
                    capabilities.pdf.embedded_assets_available
                ),
                "image_preservation": capabilities.pdf.image_preservation,
                "mixed_or_image_only_pages": (
                    capabilities.pdf.mixed_or_image_only_pages
                ),
                "warning": capabilities.pdf.warning,
            },
            execution={
                "local": capabilities.execution.local,
                "ocr": capabilities.execution.ocr,
                "hosted_fallback": capabilities.execution.hosted_fallback,
            },
        )

    return router
