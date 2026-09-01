"""Unit coverage for the deterministic real-browser service harness."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest

from markweave.jobs.models import JobOutput, JobRequest, SourceKind
from tests.browser.server import BrowserJobService


@pytest.mark.unit
def test_browser_job_service_preserves_admitted_source_metadata() -> None:
    source = b"# Browser source\n"
    request = JobRequest(
        owner_id=uuid4(),
        source=source,
        template_id=None,
        template_version_id=None,
        output=JobOutput.DOCX,
        component_versions=(("markweave", "test"),),
        now=datetime(2026, 9, 1, tzinfo=UTC),
        source_filename="source.md",
        source_kind=SourceKind.MARKDOWN,
    )

    job, replayed = BrowserJobService().submit(request, "browser-request")

    assert replayed is False
    assert job.source_filename == "source.md"
    assert job.source_kind is SourceKind.MARKDOWN
    assert job.source_sha256 == sha256(source).hexdigest()
    assert job.source_size == len(source)
