"""Deterministic branch coverage for CLI HTTP safety boundaries."""

from __future__ import annotations

from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from markweave.cli.errors import CliError
from markweave.cli.http import (
    ApiResponse,
    HttpTransport,
    _read_body,
    _response,
    _validate_cookie_name,
    _with_session_cookie,
    api_error,
)
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.unit


class _Headers:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def get_all(self, name: str, default=()):
        return self._values if name == "Set-Cookie" else default


class _Body:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, size: int) -> bytes:
        return self._body[:size]


def test_response_parsing_cookie_selection_and_safe_errors() -> None:
    """JSON, malformed payloads, configured cookies, and envelopes fail deterministically."""
    response = _response(
        200,
        _Headers(["__Host-session=opaque", "csrf=value"]),
        b'{"csrf_token":"value"}',
    )
    assert (
        _with_session_cookie(response, "__Host-session").session
        == "__Host-session=opaque"
    )
    assert _with_session_cookie(response, "missing").session is None
    assert _response(500, _Headers([]), b"\xff").payload is None
    assert (
        api_error(
            ApiResponse(401, {"error": {"code": "DENIED", "message": "No."}}),
            fallback="x",
        ).code
        == "denied"
    )
    assert api_error(ApiResponse(500, None), fallback="x").code == "x"


def test_response_limits_and_cookie_name_validation() -> None:
    """Oversized bodies and header-control characters fail before unsafe use."""
    with pytest.raises(CliError, match="too large"):
        _read_body(_Body(b"x" * 1_048_577))
    for value in ("", "bad\rname", "bad\nname"):
        with pytest.raises(CliError):
            _validate_cookie_name(value)


class _Response(_Body):
    status = 200
    headers = _Headers(["md_converter_session=session", "csrf=csrf"])

    def close(self) -> None:
        pass


def test_transport_request_branches(mocker) -> None:
    """Success, HTTP failures, network failures, and loopback proxy bypass stay safe."""
    opener = mocker.Mock(
        open=mocker.Mock(return_value=_Response(b'{"csrf_token":"csrf"}'))
    )
    build = mocker.patch("markweave.cli.http.build_opener", return_value=opener)
    mocker.patch("markweave.cli.http.ssl.create_default_context", return_value=object())
    profile = ConnectionProfile("p", "http://127.0.0.1", "md_converter_session=s", "c")
    transport = HttpTransport(profile.service_url, verify_tls=True, timeout=1)
    assert transport.login("u", "p").session == "md_converter_session=session"
    assert transport.session(profile).status == 200
    assert transport.logout(profile).status == 200
    assert transport.change_password(profile, "n", "n").status == 200
    assert any(type(item).__name__ == "ProxyHandler" for item in build.call_args.args)

    error = HTTPError("http://x", 403, "no", Message(), BytesIO(b"{}"))
    opener.open.side_effect = error
    assert transport.session(profile).status == 403
    opener.open.side_effect = URLError("down")
    with pytest.raises(CliError, match="could not be reached"):
        transport.session(profile)
