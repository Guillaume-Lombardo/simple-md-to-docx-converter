"""Stable sanitized persistence boundary errors."""


class PersistenceError(RuntimeError):
    """A database operation failed without exposing SQL or bound parameters."""

    def __init__(self) -> None:
        super().__init__("Persistence operation failed")
