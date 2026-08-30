"""HTTP and atomic-file boundaries for conversion and job CLI commands."""

from __future__ import annotations

import json
import os
import ssl
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID, uuid4

from markweave.cli.errors import CliError
from markweave.cli.profiles import validate_service_url
from markweave.cli.types import ConnectionProfile

_MAX_JSON_BYTES = 1_048_576
_DOWNLOAD_CHUNK_BYTES = 65_536
_PRIVATE_FILE_MODE = 0o600
_OK = 200


@dataclass(frozen=True)
class ConversionHttpResponse:
    """Bounded response metadata returned to the conversion command family."""

    status: int
    payload: dict[str, Any] | None = field(default=None, repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    bytes_written: int | None = None

    @property
    def correlation_id(self) -> str | None:
        """Return only a canonical server correlation UUID."""
        value = self.headers.get("x-correlation-id")
        if value is None:
            return None
        try:
            return str(UUID(value))
        except ValueError:
            return None


class _FailClosedRedirectHandler(HTTPRedirectHandler):
    """Never forward authenticated headers through an HTTP redirect."""

    def redirect_request(  # noqa: PLR0913, PLR0917 - stdlib override
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        del req, fp, code, msg, headers, newurl
        return None


class ConversionHttpClient:
    """Small HTTP-only client scoped to documented conversion endpoints."""

    def __init__(self, profile: ConnectionProfile, *, timeout: float | None) -> None:
        self._profile = profile
        self._service_url = validate_service_url(profile.service_url, verify_tls=True)
        self._timeout = timeout

    def submit(  # noqa: PLR0913 - explicit multipart API contract
        self,
        source: bytes,
        *,
        source_kind: str,
        output: str,
        template_id: str | None,
        template_version_id: str | None,
        idempotency_key: str | None,
    ) -> ConversionHttpResponse:
        """Submit one multipart conversion without revealing the local filename."""
        body, content_type = _multipart_body(
            source,
            source_kind=source_kind,
            output=output,
            template_id=template_id,
            template_version_id=template_version_id,
        )
        headers = {"Content-Type": content_type}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return self.request(
            "POST", "/api/v1/conversions", csrf=True, headers=headers, body=body
        )

    def list_jobs(self, *, offset: int, limit: int) -> ConversionHttpResponse:
        query = urlencode({"offset": offset, "limit": limit})
        return self.request("GET", f"/api/v1/conversions?{query}")

    def get_job(self, job_id: str) -> ConversionHttpResponse:
        return self.request("GET", f"/api/v1/conversions/{job_id}")

    def cancel_job(self, job_id: str) -> ConversionHttpResponse:
        return self.request("DELETE", f"/api/v1/conversions/{job_id}", csrf=True)

    def download_result(
        self, job_id: str, destination: Path, *, overwrite: bool
    ) -> ConversionHttpResponse:
        return self.download(
            f"/api/v1/conversions/{job_id}/result",
            destination,
            overwrite=overwrite,
        )

    def download_manifest(
        self, job_id: str, destination: Path, *, overwrite: bool
    ) -> ConversionHttpResponse:
        return self.download(
            f"/api/v1/conversions/{job_id}/result/manifest",
            destination,
            overwrite=overwrite,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        csrf: bool = False,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> ConversionHttpResponse:
        """Return one bounded JSON response from an API-v1 endpoint."""
        response = self._open(method, path, csrf=csrf, headers=headers, body=body)
        try:
            content = _read_bounded(response, _MAX_JSON_BYTES)
            return ConversionHttpResponse(
                response.status,
                _decode_payload(content),
                _headers(response),
            )
        finally:
            response.close()

    def download(
        self, path: str, destination: Path, *, overwrite: bool
    ) -> ConversionHttpResponse:
        """Stream a successful response into one atomic owner-only destination."""
        _validate_destination(destination, overwrite=overwrite)
        response = self._open("GET", path)
        headers = _headers(response)
        if response.status != _OK:
            try:
                content = _read_bounded(response, _MAX_JSON_BYTES)
                return ConversionHttpResponse(
                    response.status, _decode_payload(content), headers
                )
            finally:
                response.close()
        try:
            written = _atomic_stream(response, destination, overwrite=overwrite)
        except (TimeoutError, URLError, OSError) as error:
            raise CliError(
                "download_failed", "The download could not be completed."
            ) from error
        finally:
            response.close()
        return ConversionHttpResponse(
            response.status, headers=headers, bytes_written=written
        )

    def _open(
        self,
        method: str,
        path: str,
        *,
        csrf: bool = False,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> Any:
        if not path.startswith("/api/v1/") or "\r" in path or "\n" in path:
            raise CliError("invalid_request", "The API request path is invalid.")
        request_headers = {
            "Accept": "application/json",
            "Cookie": self._profile.session_state or "",
            **(headers or {}),
        }
        if csrf:
            request_headers["X-CSRF-Token"] = self._profile.csrf_state or ""
        request = Request(  # noqa: S310 - profile URL is validated above
            f"{self._service_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        handlers: list[Any] = [
            HTTPSHandler(context=ssl.create_default_context()),
            _FailClosedRedirectHandler(),
        ]
        if self._service_url.startswith("http://"):
            handlers.append(ProxyHandler({}))
        try:
            return build_opener(*handlers).open(request, timeout=self._timeout)
        except HTTPError as error:
            return error
        except (TimeoutError, URLError, OSError) as error:
            raise CliError(
                "network_error", "The service could not be reached."
            ) from error


def _multipart_body(
    source: bytes,
    *,
    source_kind: str,
    output: str,
    template_id: str | None,
    template_version_id: str | None,
) -> tuple[bytes, str]:
    boundary = f"markweave-{uuid4().hex}"
    while boundary.encode("ascii") in source:
        boundary = f"markweave-{uuid4().hex}"
    fields = [("output", output)]
    if template_id is not None and template_version_id is not None:
        fields.extend(
            (("template_id", template_id), ("template_version_id", template_version_id))
        )
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("ascii"),
                b"\r\n",
            )
        )
    media_type = "text/markdown" if source_kind == "md" else "application/zip"
    chunks.extend(
        (
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="source"; '
                f'filename="source.{source_kind}"\r\n'
            ).encode(),
            f"Content-Type: {media_type}\r\n\r\n".encode(),
            source,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _read_bounded(response: BinaryIO, limit: int) -> bytes:
    content = response.read(limit + 1)
    if len(content) > limit:
        raise CliError("response_too_large", "The service response is too large.")
    return content


def _decode_payload(content: bytes) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        payload = json.loads(content.decode("utf-8"))
    except UnicodeError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _headers(response: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in response.headers.items()}


def _validate_destination(destination: Path, *, overwrite: bool) -> None:
    parent = destination.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as error:
        raise CliError(
            "download_destination_invalid", "The download destination is invalid."
        ) from error
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(
        parent_metadata.st_mode
    ):
        raise CliError(
            "download_destination_invalid", "The download destination is invalid."
        )
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise CliError(
            "download_destination_invalid", "The download destination is invalid."
        ) from error
    if not overwrite:
        raise CliError(
            "download_exists",
            "The download destination already exists; use --overwrite to replace it.",
        )
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CliError(
            "download_destination_unsafe", "The download destination is unsafe."
        )


def _atomic_stream(response: BinaryIO, destination: Path, *, overwrite: bool) -> int:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".markweave-download-", dir=destination.parent
    )
    temporary = Path(temporary_name)
    written = 0
    try:
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            while chunk := response.read(_DOWNLOAD_CHUNK_BYTES):
                output.write(chunk)
                written += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError as error:
                raise CliError(
                    "download_exists",
                    "The download destination already exists; use --overwrite to replace it.",
                ) from error
            temporary.unlink()
        return written
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()
