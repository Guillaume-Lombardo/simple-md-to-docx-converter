"""Test guard emulating the deployed SQLite 3.34 UPDATE grammar."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, event


def enforce_sqlite_334_update_grammar(engine: Engine) -> None:
    """Fail tests if a repository emits UPDATE...RETURNING on SQLite."""

    def reject_update_returning(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.upper().split())
        if normalized.startswith("UPDATE ") and " RETURNING " in normalized:
            raise AssertionError("SQLite 3.34 does not support UPDATE...RETURNING")

    event.listen(engine, "before_cursor_execute", reject_update_returning)
