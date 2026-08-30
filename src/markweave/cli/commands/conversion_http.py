"""HTTP and atomic-file boundaries for conversion and job CLI commands."""

from __future__ import annotations

import json
import os
import ssl
import stat
import sys
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
        directory_descriptor = _open_destination(destination, overwrite=overwrite)
        response = None
        try:
            response = self._open("GET", path)
            headers = _headers(response)
            if response.status != _OK:
                content = _read_bounded(response, _MAX_JSON_BYTES)
                return ConversionHttpResponse(
                    response.status, _decode_payload(content), headers
                )
            try:
                written = _atomic_stream(
                    response,
                    destination,
                    overwrite=overwrite,
                    directory_descriptor=directory_descriptor,
                )
            except (TimeoutError, URLError, OSError) as error:
                raise CliError(
                    "download_failed", "The download could not be completed."
                ) from error
            return ConversionHttpResponse(
                response.status, headers=headers, bytes_written=written
            )
        finally:
            _close_download_resources(
                response,
                directory_descriptor,
                primary_error=sys.exception(),
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
    directory_descriptor = _open_destination(destination, overwrite=overwrite)
    os.close(directory_descriptor)


def _open_destination(destination: Path, *, overwrite: bool) -> int:
    if destination.name in {"", ".", ".."}:
        raise CliError(
            "download_destination_invalid", "The download destination is invalid."
        )
    directory_descriptor = -1
    opened = False
    try:
        directory_descriptor = os.open(
            destination.parent,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        _validate_destination_name(
            directory_descriptor, destination.name, overwrite=overwrite
        )
        opened = True
        return directory_descriptor
    except CliError:
        raise
    except OSError as error:
        raise CliError(
            "download_destination_invalid", "The download destination is invalid."
        ) from error
    finally:
        if directory_descriptor >= 0 and not opened:
            os.close(directory_descriptor)


def _validate_destination_name(
    directory_descriptor: int, name: str, *, overwrite: bool
) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
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


def _atomic_stream(
    response: BinaryIO,
    destination: Path,
    *,
    overwrite: bool,
    directory_descriptor: int | None = None,
) -> int:
    owned_directory_descriptor = directory_descriptor is None
    if directory_descriptor is None:
        directory_descriptor = _open_destination(destination, overwrite=overwrite)
    temporary = f".markweave-download-{uuid4().hex}"
    descriptor = -1
    written = 0
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            _PRIVATE_FILE_MODE,
            dir_fd=directory_descriptor,
        )
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            while chunk := response.read(_DOWNLOAD_CHUNK_BYTES):
                output.write(chunk)
                written += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(
                temporary,
                destination.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
        else:
            try:
                os.link(
                    temporary,
                    destination.name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise CliError(
                    "download_exists",
                    "The download destination already exists; use --overwrite to replace it.",
                ) from error
            os.unlink(temporary, dir_fd=directory_descriptor)
        temporary = ""
        os.fsync(directory_descriptor)
        return written
    finally:
        _cleanup_atomic_resources(
            descriptor=descriptor,
            temporary=temporary,
            directory_descriptor=directory_descriptor,
            close_directory=owned_directory_descriptor,
            primary_error=sys.exception(),
        )


def _close_download_resources(
    response: Any,
    directory_descriptor: int,
    *,
    primary_error: BaseException | None,
) -> None:
    close_error: BaseException | None = None
    try:
        if response is not None:
            response.close()
    except BaseException as error:
        close_error = error
    try:
        os.close(directory_descriptor)
    except OSError as error:
        if close_error is None:
            close_error = error
    if primary_error is None and close_error is not None:
        raise close_error


def _cleanup_atomic_resources(
    *,
    descriptor: int,
    temporary: str,
    directory_descriptor: int,
    close_directory: bool,
    primary_error: BaseException | None,
) -> None:
    cleanup_error: OSError | None = None
    try:
        if descriptor >= 0:
            os.close(descriptor)
    except OSError as error:
        cleanup_error = error
    if temporary:
        try:
            os.unlink(temporary, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        except OSError as error:
            if cleanup_error is None:
                cleanup_error = error
        try:
            os.fsync(directory_descriptor)
        except OSError as error:
            if cleanup_error is None:
                cleanup_error = error
    if close_directory:
        try:
            os.close(directory_descriptor)
        except OSError as error:
            if cleanup_error is None:
                cleanup_error = error
    if primary_error is None and cleanup_error is not None:
        raise cleanup_error
