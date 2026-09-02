"""Final OpenAPI contract decoration."""

from fastapi import FastAPI

from markweave.observability import CORRELATION_HEADER

SESSION_COOKIE_SECURITY_SCHEME = "SessionCookie"
PUBLIC_OPERATIONS = frozenset(
    {
        ("/api/v1/login", "post"),
        ("/health/live", "get"),
        ("/health/ready", "get"),
        ("/metrics", "get"),
    }
)


def document_openapi_contract(app: FastAPI, *, session_cookie_name: str) -> None:
    """Declare runtime response headers and the session-cookie security boundary."""

    schema = app.openapi()
    schema.setdefault("components", {}).setdefault("securitySchemes", {})[
        SESSION_COOKIE_SECURITY_SCHEME
    ] = {
        "type": "apiKey",
        "in": "cookie",
        "name": session_cookie_name,
        "description": "Opaque authenticated Markweave session cookie.",
    }
    schema["security"] = [{SESSION_COOKIE_SECURITY_SCHEME: []}]
    for path, method in PUBLIC_OPERATIONS:
        schema["paths"][path][method]["security"] = []

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

    template_download_headers = {
        "Cache-Control": {
            "description": "Prevents shared and private caching of template content.",
            "schema": {"type": "string", "const": "private, no-store"},
        },
        "Content-Disposition": {
            "description": "Safe attachment filename derived from immutable identifiers.",
            "schema": {"type": "string"},
        },
        "X-Content-Type-Options": {
            "description": "Prevents content-type sniffing.",
            "schema": {"type": "string", "const": "nosniff"},
        },
    }
    for path in (
        "/api/v1/templates/{template_id}/content",
        "/api/v1/templates/{template_id}/versions/{version_id}/content",
    ):
        response = schema["paths"][path]["get"]["responses"]["200"]
        response.setdefault("headers", {}).update(template_download_headers)

    policy_etag_header = {
        "description": "Current role-specific idle-session policy revision.",
        "schema": {"type": "string"},
    }
    for method in ("get", "put"):
        response = schema["paths"]["/api/v1/admin/session-policy"][method]["responses"][
            "200"
        ]
        response.setdefault("headers", {})["ETag"] = policy_etag_header
