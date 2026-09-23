"""Real SQLite presentation option and template-type contracts."""

import pytest

from markweave.persistence.migrations import upgrade_database
from markweave.persistence.sql import managed_database_engine, standalone_database_url
from tests.presentation_repository_contracts import exercise_presentation_repository

pytestmark = pytest.mark.light_coverage


@pytest.mark.light_coverage
@pytest.mark.integration
def test_sqlite_presentation_contract(tmp_path):
    with managed_database_engine(standalone_database_url(tmp_path)) as engine:
        upgrade_database(engine)
        exercise_presentation_repository(engine)
