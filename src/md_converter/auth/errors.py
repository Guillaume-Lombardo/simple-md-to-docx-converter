"""Stable functional authentication errors."""

from __future__ import annotations

from dataclasses import dataclass


class AuthenticationError(Exception):
    """An English functional error safe for an HTTP response."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """Reusable error metadata that creates a fresh exception per failure."""

    code: str
    message: str
    status_code: int

    def new(self) -> AuthenticationError:
        """Create an exception without retaining a prior request traceback."""
        return AuthenticationError(self.code, self.message, self.status_code)


INVALID_CREDENTIALS = ErrorSpec(
    "INVALID_CREDENTIALS", "The username or password is incorrect.", 401
)
AUTHENTICATION_REQUIRED = ErrorSpec(
    "AUTHENTICATION_REQUIRED", "Authentication is required.", 401
)
CSRF_REQUIRED = ErrorSpec("CSRF_REQUIRED", "A valid CSRF token is required.", 403)
LOGIN_ORIGIN_INVALID = ErrorSpec(
    "LOGIN_ORIGIN_INVALID", "The login request origin is not allowed.", 403
)
FORBIDDEN = ErrorSpec(
    "FORBIDDEN", "You are not authorized to perform this operation.", 403
)
USERNAME_INVALID = ErrorSpec("USERNAME_INVALID", "The username must not be blank.", 422)
USERNAME_TAKEN = ErrorSpec(
    "USERNAME_TAKEN", "An account with this username already exists.", 409
)
USER_NOT_FOUND = ErrorSpec("USER_NOT_FOUND", "The account was not found.", 404)
PASSWORD_INVALID = ErrorSpec("PASSWORD_INVALID", "The password must not be blank.", 422)
