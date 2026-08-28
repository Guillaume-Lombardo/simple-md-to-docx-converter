"""Unit coverage for safe server-rendered conversion HTML."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from markweave.auth.models import Role, User
from markweave.jobs.models import ConversionJob, JobOutput, JobState, JobStep
from markweave.templates.models import TemplateIdentity, TemplateStatus
from markweave.web import render_conversion_page, render_login_page


@pytest.mark.unit
def test_login_and_conversion_pages_escape_persisted_identity_text() -> None:
    actor = User(uuid4(), '<script id="user">', "normalized", "hash", Role.USER)
    template = TemplateIdentity(
        uuid4(),
        actor.id,
        '<img src=x onerror="alert(1)">',
        "Visible",
        TemplateStatus.ACTIVE,
        current_version_id=uuid4(),
    )
    now = datetime.now(UTC)
    job = ConversionJob(
        uuid4(),
        actor.id,
        uuid4(),
        template.id,
        template.current_version_id or uuid4(),
        JobOutput.DOCX,
        (("md-converter", "0.1.0"),),
        JobState.QUEUED,
        JobStep.QUEUED,
        0,
        "a" * 64,
        None,
        now,
        now,
    )

    page = render_conversion_page(
        actor,
        template,
        "Preferred template",
        (job,),
        maximum_upload_bytes=123,
    )
    assert "<script id=" not in page
    assert "<img src=x" not in page
    assert "&lt;script" in page and "&lt;img" in page
    assert 'data-max-upload-bytes="123"' in page
    assert str(template.current_version_id) in page
    assert str(job.id) in page
    assert "Templates" in page and "Users" not in page
    assert 'role="alert"' in render_login_page(invalid=True)
    assert 'role="alert"' not in render_login_page()


@pytest.mark.unit
def test_conversion_page_has_explicit_empty_states() -> None:
    actor = User(uuid4(), "Alice", "alice", "hash", Role.USER)
    page = render_conversion_page(actor, None, None, (), maximum_upload_bytes=1024)
    assert "Pandoc default" in page
    assert "No recent conversions" in page
    assert "Start conversion</button>" in page
    assert 'id="submit-conversion" type="submit" disabled' not in page
