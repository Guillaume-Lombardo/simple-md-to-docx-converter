"""Sanitized HTTP error handling and OpenAPI error metadata."""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from markweave.auth.errors import AuthenticationError
from markweave.auth.policy_errors import (
    IdleSessionPolicyAbsoluteLimitError,
    IdleSessionPolicyConflictError,
    IdleSessionPolicyPreconditionRequiredError,
)
from markweave.composer.connections import (
    ConnectionAuthorizationError,
    ConnectionConfigurationError,
    ConnectionConflictError,
    ConnectionNotFoundError,
)
from markweave.composer.egress import (
    EgressCapacityError,
    EgressPolicyError,
    EgressResponseError,
    EgressUnavailableError,
)
from markweave.composer.revisions import (
    ComposerArtifactError,
    ComposerConflictError,
    ComposerNotFoundError,
)
from markweave.composer.secrets import SecretError
from markweave.composer.typed_templates import TypedTemplateError
from markweave.jobs.errors import (
    JobConflictError,
    JobNotFoundError,
    JobQueueCapacityExceededError,
    JobRepositoryError,
    JobRequestError,
    JobUserQuotaExceededError,
)
from markweave.malware import MalwareDetectedError, MalwareScannerUnavailableError
from markweave.persistence.composer import ComposerCapacityError
from markweave.persistence.composer.typed_templates import (
    TypedTemplateArtifactError,
    TypedTemplateConflictError,
    TypedTemplateNotFoundError,
)
from markweave.persistence.errors import PersistenceError
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobNotFoundError,
    ReversionJobRepositoryError,
    ReversionJobRequestError,
    ReversionJobStorageError,
    ReversionJobUserQuotaExceededError,
    ReversionQueueCapacityExceededError,
    ReversionServiceUnavailableError,
)
from markweave.reversions.capabilities import ReversionCapabilitiesUnavailableError
from markweave.reversions.errors import ReverseConversionError
from markweave.storage import ObjectStoreError
from markweave.templates.errors import (
    TemplateConflictError,
    TemplateIntegrityError,
    TemplatePreconditionRequiredError,
    TemplateRequestError,
    TemplateStorageError,
    TemplateUnavailableError,
    TemplateValidationError,
    TemplateValidationErrorCode,
)

from .composer_errors import (
    ComposerPreconditionInvalidError,
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerRequestTooLargeError,
    ComposerUnavailableError,
)
from .schemas import ErrorResponse

ERROR_DESCRIPTIONS = {
    401: "Authentication failed or is required",
    403: "The operation is forbidden",
    404: "The requested resource was not found",
    409: "The request conflicts with current state",
    412: "A request precondition failed",
    413: "The request body is too large",
    422: "The request is invalid",
    428: "The request requires a precondition",
    429: "The caller has exceeded a configured quota",
    502: "The upstream service returned an invalid response",
    503: "The service is not ready",
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build explicit OpenAPI entries for stable error envelopes."""
    return {
        status_code: {
            "model": ErrorResponse,
            "description": ERROR_DESCRIPTIONS[status_code],
        }
        for status_code in status_codes
    }


def capacity_error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Document the retry header on Composer routes with model-call capacity."""

    responses = error_responses(*status_codes)
    if status.HTTP_503_SERVICE_UNAVAILABLE in responses:
        responses[status.HTTP_503_SERVICE_UNAVAILABLE]["headers"] = {
            "Retry-After": {"schema": {"type": "string"}}
        }
    return responses


def install_error_handlers(app: FastAPI) -> None:
    """Install sanitized functional and request-validation error handlers."""

    @app.exception_handler(AuthenticationError)
    def authentication_error_handler(
        _request: Request, error: AuthenticationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
        )

    @app.exception_handler(RequestValidationError)
    def request_validation_error_handler(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "REQUEST_INVALID",
                    "message": "The request is invalid.",
                }
            },
        )

    for error_class, http_status, code, message in (
        (
            ConnectionAuthorizationError,
            403,
            "COMPOSER_FORBIDDEN",
            "This Composer operation is not permitted.",
        ),
        (
            ConnectionConflictError,
            412,
            "COMPOSER_PRECONDITION_FAILED",
            "The Composer resource has changed.",
        ),
        (
            ConnectionNotFoundError,
            404,
            "COMPOSER_CONNECTION_NOT_FOUND",
            "The Composer connection was not found.",
        ),
        (
            ConnectionConfigurationError,
            422,
            "COMPOSER_CONNECTION_INVALID",
            "The Composer connection is invalid.",
        ),
        (
            EgressPolicyError,
            422,
            "COMPOSER_DESTINATION_REJECTED",
            "The model destination is not permitted.",
        ),
        (
            EgressCapacityError,
            503,
            "COMPOSER_CAPACITY_EXHAUSTED",
            "Composer model call capacity is temporarily exhausted.",
        ),
        (
            EgressUnavailableError,
            503,
            "COMPOSER_PROVIDER_UNAVAILABLE",
            "The model provider is temporarily unavailable.",
        ),
        (
            EgressResponseError,
            502,
            "COMPOSER_PROVIDER_RESPONSE_INVALID",
            "The model provider returned an invalid response.",
        ),
        (
            ComposerPreconditionRequiredError,
            428,
            "COMPOSER_PRECONDITION_REQUIRED",
            "If-Match is required.",
        ),
        (
            ComposerPreconditionInvalidError,
            422,
            "COMPOSER_PRECONDITION_INVALID",
            "If-Match is invalid.",
        ),
        (
            ComposerUnavailableError,
            503,
            "COMPOSER_SERVICE_UNAVAILABLE",
            "Composer is unavailable.",
        ),
        (
            ComposerRequestError,
            422,
            "COMPOSER_REQUEST_INVALID",
            "The Composer request is invalid.",
        ),
        (
            ComposerRequestTooLargeError,
            413,
            "COMPOSER_UPLOAD_TOO_LARGE",
            "The Composer source exceeds the configured limit.",
        ),
        (
            ComposerNotFoundError,
            404,
            "COMPOSER_RESOURCE_NOT_FOUND",
            "The Composer resource was not found.",
        ),
        (
            ComposerCapacityError,
            503,
            "COMPOSER_CAPACITY_EXHAUSTED",
            "Composer model call capacity is temporarily exhausted.",
        ),
        (
            ComposerConflictError,
            412,
            "COMPOSER_PRECONDITION_FAILED",
            "The Composer resource has changed.",
        ),
        (
            ComposerArtifactError,
            503,
            "COMPOSER_ARTIFACT_UNAVAILABLE",
            "The Composer artifact is unavailable.",
        ),
        (
            TypedTemplateNotFoundError,
            404,
            "COMPOSER_TYPED_TEMPLATE_NOT_FOUND",
            "The typed template was not found.",
        ),
        (
            TypedTemplateConflictError,
            412,
            "COMPOSER_TYPED_TEMPLATE_CHANGED",
            "The typed template has changed.",
        ),
        (
            TypedTemplateArtifactError,
            503,
            "COMPOSER_TYPED_TEMPLATE_UNAVAILABLE",
            "The typed template content is unavailable.",
        ),
        (
            SecretError,
            503,
            "COMPOSER_CREDENTIAL_UNAVAILABLE",
            "The model credential is unavailable.",
        ),
    ):

        def handler(
            request: Request,
            _error: Exception,
            *,
            response_status: int = http_status,
            response_code: str = code,
            response_message: str = message,
        ) -> JSONResponse:
            headers = {"Cache-Control": "private, no-store"}
            if response_status == status.HTTP_503_SERVICE_UNAVAILABLE:
                headers["Retry-After"] = str(
                    request.app.state.composer_retry_after_seconds
                )
            if isinstance(_error, ComposerCapacityError):
                request.app.state.components.metrics.record_composer_model_step_saturation(
                    "admission"
                )
            elif isinstance(_error, EgressCapacityError):
                request.app.state.components.metrics.record_composer_model_step_saturation(
                    "egress"
                )
            return JSONResponse(
                status_code=response_status,
                headers=headers,
                content={"error": {"code": response_code, "message": response_message}},
            )

        app.add_exception_handler(error_class, handler)

    @app.exception_handler(TypedTemplateError)
    def typed_template_validation_handler(
        _request: Request, error: TypedTemplateError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=413 if error.code == "limit_exceeded" else 422,
            content={
                "error": {
                    "code": f"COMPOSER_TYPED_{error.code.upper()}",
                    "message": "The typed template or values are invalid.",
                    "paths": list(error.paths),
                }
            },
            headers={"Cache-Control": "private, no-store"},
        )

    @app.exception_handler(PersistenceError)
    def persistence_error_handler(
        _request: Request, _error: PersistenceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "PERSISTENCE_UNAVAILABLE",
                    "message": "Persistent storage is unavailable.",
                }
            },
        )

    @app.exception_handler(ReversionCapabilitiesUnavailableError)
    def reversion_capabilities_unavailable_handler(
        _request: Request, _error: ReversionCapabilitiesUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
            content={
                "error": {
                    "code": "REVERSION_CAPABILITIES_UNAVAILABLE",
                    "message": "Reverse-conversion capabilities are unavailable.",
                }
            },
        )

    @app.exception_handler(IdleSessionPolicyPreconditionRequiredError)
    def idle_policy_precondition_handler(
        _request: Request, _error: IdleSessionPolicyPreconditionRequiredError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=428,
            content={
                "error": {
                    "code": "IDLE_SESSION_POLICY_PRECONDITION_REQUIRED",
                    "message": "If-Match is required.",
                }
            },
        )

    @app.exception_handler(IdleSessionPolicyConflictError)
    def idle_policy_conflict_handler(
        _request: Request, _error: IdleSessionPolicyConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=412,
            content={
                "error": {
                    "code": "IDLE_SESSION_POLICY_PRECONDITION_FAILED",
                    "message": "The idle-session policy has changed.",
                }
            },
        )

    @app.exception_handler(IdleSessionPolicyAbsoluteLimitError)
    def idle_policy_absolute_limit_handler(
        _request: Request, _error: IdleSessionPolicyAbsoluteLimitError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "IDLE_SESSION_POLICY_EXCEEDS_ABSOLUTE_LIFETIME",
                    "message": "Idle-session durations cannot exceed the absolute lifetime.",
                }
            },
        )

    @app.exception_handler(JobNotFoundError)
    def job_not_found_handler(
        _request: Request, _error: JobNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": {
                    "code": "CONVERSION_NOT_FOUND",
                    "message": "The conversion was not found.",
                }
            },
        )

    @app.exception_handler(JobConflictError)
    def job_conflict_handler(
        _request: Request, _error: JobConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": {
                    "code": "CONVERSION_CONFLICT",
                    "message": "The conversion request conflicts with current state.",
                }
            },
        )

    @app.exception_handler(JobRequestError)
    def job_request_handler(_request: Request, _error: JobRequestError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "CONVERSION_REQUEST_INVALID",
                    "message": "The conversion request is invalid.",
                }
            },
        )

    @app.exception_handler(MalwareDetectedError)
    def malware_detected_handler(
        _request: Request, _error: MalwareDetectedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "UPLOAD_MALWARE_DETECTED",
                    "message": "The upload was rejected by malware scanning.",
                }
            },
        )

    @app.exception_handler(MalwareScannerUnavailableError)
    def malware_scanner_unavailable_handler(
        _request: Request, _error: MalwareScannerUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "UPLOAD_SCANNER_UNAVAILABLE",
                    "message": "Upload malware scanning is unavailable.",
                }
            },
        )

    @app.exception_handler(JobUserQuotaExceededError)
    def job_user_quota_handler(
        request: Request, _error: JobUserQuotaExceededError
    ) -> JSONResponse:
        request.app.state.components.metrics.record_saturation("owner")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(request.app.state.conversion_retry_after_seconds)
            },
            content={
                "error": {
                    "code": "CONVERSION_USER_QUOTA_EXCEEDED",
                    "message": "The active conversion quota is exhausted.",
                }
            },
        )

    @app.exception_handler(JobQueueCapacityExceededError)
    def job_queue_capacity_handler(
        request: Request, _error: JobQueueCapacityExceededError
    ) -> JSONResponse:
        request.app.state.components.metrics.record_saturation("global")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={
                "Retry-After": str(request.app.state.conversion_retry_after_seconds)
            },
            content={
                "error": {
                    "code": "CONVERSION_QUEUE_CAPACITY_EXCEEDED",
                    "message": "The conversion queue is at capacity.",
                }
            },
        )

    @app.exception_handler(JobRepositoryError)
    @app.exception_handler(ObjectStoreError)
    def job_storage_error_handler(
        _request: Request, _error: JobRepositoryError | ObjectStoreError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "CONVERSION_STORAGE_UNAVAILABLE",
                    "message": "Conversion storage is unavailable.",
                }
            },
        )

    @app.exception_handler(ReversionServiceUnavailableError)
    def reversion_unavailable_handler(
        _request: Request, _error: ReversionServiceUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
            content={
                "error": {
                    "code": "REVERSION_SERVICE_UNAVAILABLE",
                    "message": "Reverse conversion is unavailable.",
                }
            },
        )

    @app.exception_handler(ReversionJobNotFoundError)
    def reversion_not_found_handler(
        _request: Request, _error: ReversionJobNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": {
                    "code": "REVERSION_NOT_FOUND",
                    "message": "The reverse conversion was not found.",
                }
            },
        )

    @app.exception_handler(ReversionJobConflictError)
    def reversion_conflict_handler(
        _request: Request, _error: ReversionJobConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": {
                    "code": "REVERSION_CONFLICT",
                    "message": "The reverse conversion conflicts with current state.",
                }
            },
        )

    @app.exception_handler(ReversionJobRequestError)
    @app.exception_handler(ReverseConversionError)
    def reversion_request_handler(
        _request: Request, _error: ReversionJobRequestError | ReverseConversionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "REVERSION_REQUEST_INVALID",
                    "message": "The reverse-conversion request is invalid.",
                }
            },
        )

    @app.exception_handler(ReversionJobUserQuotaExceededError)
    def reversion_quota_handler(
        request: Request, _error: ReversionJobUserQuotaExceededError
    ) -> JSONResponse:
        request.app.state.components.metrics.record_saturation("reversion_owner")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(request.app.state.reversion_retry_after_seconds)
            },
            content={
                "error": {
                    "code": "REVERSION_USER_QUOTA_EXCEEDED",
                    "message": "The active reverse-conversion quota is exhausted.",
                }
            },
        )

    @app.exception_handler(ReversionQueueCapacityExceededError)
    def reversion_capacity_handler(
        request: Request, _error: ReversionQueueCapacityExceededError
    ) -> JSONResponse:
        request.app.state.components.metrics.record_saturation("global")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={
                "Retry-After": str(request.app.state.reversion_retry_after_seconds)
            },
            content={
                "error": {
                    "code": "REVERSION_QUEUE_CAPACITY_EXCEEDED",
                    "message": "The conversion queue is at capacity.",
                }
            },
        )

    @app.exception_handler(ReversionJobRepositoryError)
    @app.exception_handler(ReversionJobStorageError)
    def reversion_storage_handler(
        _request: Request,
        _error: ReversionJobRepositoryError | ReversionJobStorageError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "REVERSION_STORAGE_UNAVAILABLE",
                    "message": "Reverse-conversion storage is unavailable.",
                }
            },
        )

    @app.exception_handler(TemplateUnavailableError)
    def template_unavailable_handler(
        _request: Request, _error: TemplateUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "TEMPLATE_NOT_FOUND",
                    "message": "The template was not found.",
                }
            },
        )

    @app.exception_handler(TemplatePreconditionRequiredError)
    def template_precondition_handler(
        _request: Request, _error: TemplatePreconditionRequiredError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=428,
            content={
                "error": {
                    "code": "TEMPLATE_PRECONDITION_REQUIRED",
                    "message": "If-Match is required.",
                }
            },
        )

    @app.exception_handler(TemplateConflictError)
    def template_conflict_handler(
        _request: Request, _error: TemplateConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=412,
            content={
                "error": {
                    "code": "TEMPLATE_PRECONDITION_FAILED",
                    "message": "The template has changed or the operation is not allowed.",
                }
            },
        )

    @app.exception_handler(TemplateValidationError)
    def template_validation_handler(
        _request: Request, error: TemplateValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=(
                413 if error.code is TemplateValidationErrorCode.LIMIT_EXCEEDED else 422
            ),
            content={
                "error": {
                    "code": f"TEMPLATE_{error.code.value.upper()}",
                    "message": str(error),
                }
            },
        )

    @app.exception_handler(TemplateIntegrityError)
    def template_integrity_handler(
        _request: Request, _error: TemplateIntegrityError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "TEMPLATE_INTEGRITY_FAILURE",
                    "message": "Template content integrity verification failed.",
                }
            },
        )

    @app.exception_handler(TemplateStorageError)
    def template_storage_handler(
        _request: Request, _error: TemplateStorageError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "TEMPLATE_STORAGE_UNAVAILABLE",
                    "message": "Template storage is unavailable.",
                }
            },
        )

    @app.exception_handler(TemplateRequestError)
    def template_request_handler(
        _request: Request, _error: TemplateRequestError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "TEMPLATE_REQUEST_INVALID",
                    "message": "The template request is invalid.",
                }
            },
        )
