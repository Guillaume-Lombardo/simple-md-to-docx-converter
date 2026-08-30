"""Small standard-library HTTP boundary for remote Markweave commands."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from markweave.cli.errors import CliError
from markweave.cli.profiles import validate_service_url
from markweave.cli.types import ConnectionProfile


@dataclass(frozen=True)
class ApiResponse:
    """Decoded bounded response data needed by the authentication family."""

    status: int
    payload: dict[str, Any] | None
    session: str | None = None


class HttpTransport:
    """HTTP-only client with explicit cookie and CSRF handling."""

    def __init__(
        self, service_url: str, *, verify_tls: bool, timeout: float | None
    ) -> None:
        self._service_url = validate_service_url(service_url, verify_tls=verify_tls)
        self._verify_tls = verify_tls
        self._timeout = timeout

    def login(self, username: str, password: str) -> ApiResponse:
        return self._request(
            "POST", "/api/v1/login", body={"username": username, "password": password}
        )

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
            opener = build_opener(HTTPSHandler(context=context))
            response = opener.open(request, timeout=self._timeout)
            try:
                return _response(response.status, response.headers, response.read())
            finally:
                response.close()
        except HTTPError as error:
            try:
                return _response(error.code, error.headers, error.read())
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
            decoded = json.loads(body)
        except TypeError, ValueError:
            decoded = None
        if isinstance(decoded, dict):
            payload = decoded
    cookies = SimpleCookie()
    for value in headers.get_all("Set-Cookie", []):
        cookies.load(value)
    session_cookie = next(
        (cookie for name, cookie in cookies.items() if not name.startswith("__Host-")),
        None,
    )
    return ApiResponse(
        status=status,
        payload=payload,
        session=(
            f"{session_cookie.key}={session_cookie.value}"
            if session_cookie is not None
            else None
        ),
    )


def api_error(response: ApiResponse, *, fallback: str) -> CliError:
    """Turn a server envelope into a stable local failure without printing payloads."""
    error = response.payload.get("error") if response.payload is not None else None
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return CliError(code.lower(), message)
    return CliError(fallback, "The service rejected the request.")
