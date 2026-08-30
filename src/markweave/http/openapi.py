"""Final OpenAPI contract decoration."""

from fastapi import FastAPI

from markweave.observability import CORRELATION_HEADER


def document_correlation_headers(app: FastAPI) -> None:
    """Declare the middleware-generated correlation header on every response."""

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
