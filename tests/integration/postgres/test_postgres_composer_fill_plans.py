"""Distributed SQL/S3 fill-plan publication and restart linkage."""

import os

import pytest

from markweave.persistence.composer.fill_plans import SqlFillPlanRepository
from markweave.persistence.sql import create_database_engine
from tests.integration.postgres.test_postgres_composer_foundations import _store
from tests.integration.sqlite.test_composer_fill_plans import (
    create_plan,
    prepare,
    publish_filled_revision,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


def test_postgres_s3_fill_plan_decision_and_crash_link() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    objects = _store()
    try:
        context = prepare(engine, objects)
        plan = create_plan(context)
        approved = context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=plan.etag,
            idempotency_key="approve",
            values=plan.values,
            provenance=plan.provenance,
            questions=(),
            approve=True,
        )
        assert approved.state == "approved"
        revision_id = publish_filled_revision(context, approved)
        restarted = SqlFillPlanRepository(engine)
        linked = restarted.mark_published(
            context.owner, context.draft_id, approved.id, revision_id
        )
        assert linked.state == "published" and linked.result_revision_id == revision_id
        assert (
            restarted.mark_published(
                context.owner, context.draft_id, approved.id, revision_id
            )
            == linked
        )
    finally:
        objects.close()
