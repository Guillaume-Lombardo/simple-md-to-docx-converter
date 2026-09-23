"""PostgreSQL first-write policy contention returns an HTTP-mappable conflict."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, event
from sqlalchemy.orm import Session

from markweave.composer.connections import ConnectionConflictError
from markweave.persistence.composer.admin_policy import (
    SqlComposerAdminPolicyRepository,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import ComposerAdminPolicyRow
from markweave.persistence.sql import create_database_engine

pytestmark = [pytest.mark.integration, pytest.mark.requires_postgres]


def test_initial_policy_compare_and_swap_has_one_winner() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    repository = SqlComposerAdminPolicyRepository(engine)
    barrier = Barrier(2)

    def synchronize_first_insert(
        session: Session, _flush_context: object, _instances: object
    ) -> None:
        if any(isinstance(item, ComposerAdminPolicyRow) for item in session.new):
            barrier.wait(timeout=10)

    def write() -> str:
        try:
            repository.put(
                enabled=True,
                destinations=("models.example.test:443",),
                networks=("192.0.2.10/32",),
                expected_version=0,
                actor_id=uuid4(),
                default_enabled=False,
            )
        except ConnectionConflictError:
            return "conflict"
        return "saved"

    try:
        with Session(engine) as database, database.begin():
            database.execute(delete(ComposerAdminPolicyRow))
        event.listen(Session, "before_flush", synchronize_first_insert)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = tuple(pool.map(lambda _: write(), range(2)))
        finally:
            event.remove(Session, "before_flush", synchronize_first_insert)
        assert sorted(results) == ["conflict", "saved"]
        saved = repository.get()
        assert saved is not None
        assert saved.version == 1
    finally:
        engine.dispose()
