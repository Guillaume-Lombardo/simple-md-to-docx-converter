"""HTTP-only request bounding middleware."""

from fastapi import status
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BoundedRequestBody:
    """Bound upload request bytes before multipart parsing or spooling."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        conversion_maximum_bytes: int,
        template_maximum_bytes: int,
        template_metadata_maximum_bytes: int,
    ) -> None:
        self._app = app
        self._conversion_maximum_bytes = conversion_maximum_bytes
        self._template_maximum_bytes = template_maximum_bytes
        self._template_metadata_maximum_bytes = template_metadata_maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        template_upload = (method == "POST" and path == "/api/v1/templates") or (
            method == "PUT"
            and path.startswith("/api/v1/templates/")
            and path.endswith("/content")
        )
        if method == "POST" and path == "/api/v1/conversions":
            maximum_bytes = self._conversion_maximum_bytes
            error_code = "CONVERSION_REQUEST_TOO_LARGE"
            error_message = "The conversion request is too large."
        elif template_upload:
            maximum_bytes = self._template_maximum_bytes
            error_code = "TEMPLATE_REQUEST_TOO_LARGE"
            error_message = "The template request is too large."
        elif method == "PATCH" and path.startswith("/api/v1/templates/"):
            maximum_bytes = self._template_metadata_maximum_bytes
            error_code = "TEMPLATE_REQUEST_TOO_LARGE"
            error_message = "The template request is too large."
        else:
            await self._app(scope, receive, send)
            return
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > maximum_bytes:
                await self._reject(send, error_code, error_message)
                return
            body.extend(chunk)
            more_body = bool(message.get("more_body", False))
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self._app(scope, replay, send)

    @staticmethod
    async def _reject(send: Send, code: str, message: str) -> None:
        content = f'{{"error":{{"code":"{code}","message":"{message}"}}}}'.encode()
        await send(
            {
                "type": "http.response.start",
                "status": status.HTTP_413_CONTENT_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(content)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": content})
