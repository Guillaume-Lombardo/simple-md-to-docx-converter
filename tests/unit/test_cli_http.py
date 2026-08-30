"""Deterministic branch coverage for CLI HTTP safety boundaries."""

from __future__ import annotations

import pytest

from markweave.cli.errors import CliError
from markweave.cli.http import (
    ApiResponse,
    _read_body,
    _response,
    _validate_cookie_name,
    _with_session_cookie,
    api_error,
)

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
