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
from markweave.jobs.errors import (
    JobConflictError,
    JobNotFoundError,
    JobQueueCapacityExceededError,
    JobRepositoryError,
    JobRequestError,
    JobUserQuotaExceededError,
)
from markweave.malware import MalwareDetectedError, MalwareScannerUnavailableError
from markweave.persistence.errors import PersistenceError
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
