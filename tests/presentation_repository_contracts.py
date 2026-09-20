"""PowerPoint immutability and typed admission shared by SQL profiles."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from markweave.auth.models import Role, User
from markweave.jobs.errors import JobRequestError
from markweave.jobs.models import JobOutput
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import downgrade_database
from markweave.persistence.schema import ConversionJobRow, TemplateRow
from markweave.persistence.sql import SqlUserRepository
from markweave.presentations.models import PresentationDialect, PresentationOptions
from tests.job_repository_contracts import submission
from tests.template_records import publish_template_pair


def exercise_presentation_repository(engine):
    owner = User(uuid4(), "Presentations", uuid4().hex, "hash:fixture", Role.USER)
    SqlUserRepository(engine).create(owner)
    template_id, version_id = uuid4(), uuid4()
    publish_template_pair(engine, owner.id, template_id, version_id)
    repository = SqlJobRepository(engine)
    options = PresentationOptions(PresentationDialect.MARP, 3)
    request = replace(
        submission(owner.id),
        output=JobOutput.PPTX,
        presentation_options=options,
        template_id=template_id,
        template_version_id=version_id,
    )
    with pytest.raises(JobRequestError):
        repository.create(request)
    job, replayed = repository.create(
        replace(request, template_id=None, template_version_id=None)
    )
    assert not replayed
    loaded = repository.get(job.id)
    assert loaded is not None and loaded.presentation_options == options
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            update(ConversionJobRow)
            .where(ConversionJobRow.id == str(job.id))
            .values(presentation_options=PresentationOptions().canonical_json())
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            update(TemplateRow)
            .where(TemplateRow.id == str(template_id))
            .values(kind="pptx")
        )

    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        downgrade_database(engine, "20260906_17")
    assert repository.get(job.id) == loaded
