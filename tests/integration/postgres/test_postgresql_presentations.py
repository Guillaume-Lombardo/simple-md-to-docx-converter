"""Real PostgreSQL presentation option and template-type contracts."""

import os

import pytest

from markweave.persistence.migrations import upgrade_database
from markweave.persistence.sql import managed_database_engine
from tests.presentation_repository_contracts import exercise_presentation_repository


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_presentation_contract():
    with managed_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"]) as engine:
        upgrade_database(engine)
        exercise_presentation_repository(engine)
