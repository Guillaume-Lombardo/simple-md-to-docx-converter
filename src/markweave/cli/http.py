"""Small standard-library HTTP boundary for remote Markweave commands."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, field, replace
from http.cookies import SimpleCookie
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from markweave.cli.errors import CliError
from markweave.cli.profiles import validate_service_url
from markweave.cli.types import ConnectionProfile

_MAX_RESPONSE_BYTES = 1_048_576
_DEFAULT_SESSION_COOKIE_NAME = "md_converter_session"
_ASCII_CONTROL_MAX = 32
_ASCII_DELETE = 127


@dataclass(frozen=True)
class ApiResponse:
    """Decoded bounded response data needed by the authentication family."""

    status: int
    payload: dict[str, Any] | None = field(repr=False)
    session: str | None = field(default=None, repr=False)
    cookies: tuple[tuple[str, str], ...] = field(default=(), repr=False)


class _FailClosedRedirectHandler(HTTPRedirectHandler):
    """Prevent credentials from crossing an origin or protocol boundary on redirect."""

    def redirect_request(  # noqa: PLR0913, PLR0917 - stdlib override has a fixed signature
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        del req, fp, code, msg, headers, newurl


class HttpTransport:
    """HTTP-only client with explicit cookie and CSRF handling."""

    def __init__(
        self,
        service_url: str,
        *,
        verify_tls: bool,
        timeout: float | None,
        session_cookie_name: str = _DEFAULT_SESSION_COOKIE_NAME,
    ) -> None:
        self._service_url = validate_service_url(service_url, verify_tls=verify_tls)
        self._verify_tls = verify_tls
        self._timeout = timeout
        self._session_cookie_name = _validate_cookie_name(session_cookie_name)

    def login(
        self,
        username: str,
        password: str,
        *,
        previous_profile: ConnectionProfile | None = None,
    ) -> ApiResponse:
        response = self._request(
            "POST",
            "/api/v1/login",
            profile=previous_profile,
            body={"username": username, "password": password},
        )
        return _with_session_cookie(response, self._session_cookie_name)

    def session(self, profile: ConnectionProfile) -> ApiResponse:
        return self._request("GET", "/api/v1/session", profile=profile)

    def logout(self, profile: ConnectionProfile) -> ApiResponse:
        return self._request("POST", "/api/v1/logout", profile=profile, csrf=True)

    def change_password(
        self, profile: ConnectionProfile, password: str, confirmation: str
    ) -> ApiResponse:
        return self._request(
            "POST",
            "/api/v1/password",
            profile=profile,
            csrf=True,
            body={"password": password, "confirmation": confirmation},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        profile: ConnectionProfile | None = None,
        csrf: bool = False,
        body: dict[str, str] | None = None,
    ) -> ApiResponse:
        headers = {"Accept": "application/json"}
        if profile is not None:
            headers["Cookie"] = profile.session_state or ""
        if csrf and profile is not None:
            headers["X-CSRF-Token"] = profile.csrf_state or ""
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(  # noqa: S310 - validated HTTPS base URL
            f"{self._service_url}{path}",
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            context = ssl.create_default_context()
            handlers: list[Any] = [
                HTTPSHandler(context=context),
                _FailClosedRedirectHandler(),
            ]
            if self._service_url.startswith("http://"):
                handlers.append(ProxyHandler({}))
            opener = build_opener(*handlers)
            response = opener.open(request, timeout=self._timeout)
            try:
                return _response(
                    response.status, response.headers, _read_body(response)
                )
            finally:
                response.close()
        except HTTPError as error:
            try:
                return _response(error.code, error.headers, _read_body(error))
            finally:
                error.close()
        except (TimeoutError, URLError, OSError) as error:
            raise CliError(
                "network_error", "The service could not be reached."
            ) from error


def _response(status: int, headers: Any, body: bytes) -> ApiResponse:
    payload: dict[str, Any] | None = None
    if body:
        try:
            decoded = json.loads(body.decode("utf-8"))
        except TypeError, UnicodeError, ValueError:
            decoded = None
        if isinstance(decoded, dict):
            payload = decoded
    cookies = SimpleCookie()
    for value in headers.get_all("Set-Cookie", []):
        cookies.load(value)
    return ApiResponse(
        status=status,
        payload=payload,
        cookies=tuple((cookie.key, cookie.value) for cookie in cookies.values()),
    )


def _with_session_cookie(
    response: ApiResponse, session_cookie_name: str
) -> ApiResponse:
    """Extract only the explicitly configured session cookie, including __Host names."""
    session = next(
        (
            f"{name}={value}"
            for name, value in response.cookies
            if name == session_cookie_name and value
        ),
        None,
    )
    return replace(response, session=session)


def _read_body(response: Any) -> bytes:
    """Bound every successful and error response before decoding JSON."""
    body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise CliError("response_too_large", "The service response is too large.")
    return body


def _validate_cookie_name(value: str) -> str:
    if not value or any(
        ord(character) <= _ASCII_CONTROL_MAX or ord(character) == _ASCII_DELETE
        for character in value
    ):
        raise CliError(
            "invalid_session_cookie_name", "The session cookie name is invalid."
        )
    return value


def api_error(response: ApiResponse, *, fallback: str) -> CliError:
    """Turn a server envelope into a stable local failure without printing payloads."""
    error = response.payload.get("error") if response.payload is not None else None
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return CliError(code.lower(), message)
    return CliError(fallback, "The service rejected the request.")
