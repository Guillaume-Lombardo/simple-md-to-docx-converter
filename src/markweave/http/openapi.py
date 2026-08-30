"""Final OpenAPI contract decoration."""

from fastapi import FastAPI

from markweave.observability import CORRELATION_HEADER


def document_correlation_headers(app: FastAPI) -> None:
    """Declare middleware and handler-generated headers on documented responses."""

    schema = app.openapi()
    header = {
        "description": "Server-generated request correlation identifier.",
        "schema": {"type": "string", "format": "uuid"},
    }
    for path in schema["paths"].values():
        for operation_name, operation in path.items():
            if operation_name not in {
                "get",
                "put",
                "post",
                "delete",
                "options",
                "head",
                "patch",
                "trace",
            }:
                continue
            for response in operation["responses"].values():
                response.setdefault("headers", {})[CORRELATION_HEADER] = header

    etag_header = {
        "description": "Current template identity or immutable content validator.",
        "schema": {"type": "string"},
    }
    template_successes = {
        ("/api/v1/templates", "post"): "201",
        ("/api/v1/templates/{template_id}", "get"): "200",
        ("/api/v1/templates/{template_id}", "patch"): "200",
        ("/api/v1/templates/{template_id}/content", "get"): "200",
        ("/api/v1/templates/{template_id}/content", "put"): "201",
        (
            "/api/v1/templates/{template_id}/versions/{version_id}/content",
            "get",
        ): "200",
        (
            "/api/v1/templates/{template_id}/versions/{version_id}/restore",
            "post",
        ): "201",
        ("/api/v1/templates/{template_id}/archive", "post"): "200",
    }
    for (path, method), status_code in template_successes.items():
        response = schema["paths"][path][method]["responses"][status_code]
        response.setdefault("headers", {})["ETag"] = etag_header
