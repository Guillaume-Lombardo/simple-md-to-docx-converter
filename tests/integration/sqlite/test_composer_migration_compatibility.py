"""T90 schema upgrades and rollback use supported SQLite 3.34 grammar."""

from pathlib import Path

import pytest
from sqlalchemy import inspect

from markweave.persistence.migrations import downgrade_database, upgrade_database
from markweave.persistence.sql import create_database_engine, standalone_database_url
from tests.sqlite_compatibility import enforce_sqlite_334_alter_grammar

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def test_composer_schema_round_trip_on_sqlite_334_grammar(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    enforce_sqlite_334_alter_grammar(engine)
    try:
        upgrade_database(engine)
        head = inspect(engine)
        assert head.has_table("composer_generations")
        assert head.has_table("composer_questions")
        assert "kind" in {
            column["name"] for column in head.get_columns("composer_sources")
        }
        assert {"intent", "answered_question_id", "question_id"} <= {
            column["name"] for column in head.get_columns("composer_model_steps")
        }

        downgrade_database(engine, "20260923_21")
        earlier = inspect(engine)
        assert not earlier.has_table("composer_generations")
        assert not earlier.has_table("composer_questions")
        assert "kind" not in {
            column["name"] for column in earlier.get_columns("composer_sources")
        }
        assert {"intent", "answered_question_id", "question_id"}.isdisjoint(
            column["name"] for column in earlier.get_columns("composer_model_steps")
        )

        upgrade_database(engine)
        restored = inspect(engine)
        assert restored.has_table("composer_generations")
        assert restored.has_table("composer_questions")
        assert "kind" in {
            column["name"] for column in restored.get_columns("composer_sources")
        }
    finally:
        engine.dispose()
