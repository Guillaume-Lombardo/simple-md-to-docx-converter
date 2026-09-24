"""Pure Composer invariants before persistence and object publication."""

from uuid import uuid4

import pytest

from markweave.composer.revisions import ArtifactContent, SourceReference

pytestmark = pytest.mark.unit


def test_source_requires_exact_scan_proof_and_digest() -> None:
    owner = uuid4()
    with pytest.raises(ValueError, match="scan proof"):
        SourceReference("upload", uuid4(), owner, "a" * 64, " ", "text/markdown")
    with pytest.raises(ValueError, match="SHA-256"):
        SourceReference(
            "upload", uuid4(), owner, "not-a-digest", "scan-pass", "text/markdown"
        )
    with pytest.raises(ValueError, match="kind"):
        SourceReference(
            "remote_url", uuid4(), owner, "a" * 64, "scan-pass", "text/markdown"
        )
    with pytest.raises(ValueError, match="media type"):
        SourceReference(
            "upload", uuid4(), owner, "a" * 64, "scan-pass", "application/javascript"
        )
    with pytest.raises(ValueError, match="origin reference"):
        SourceReference(
            "upload",
            uuid4(),
            owner,
            "a" * 64,
            "scan-pass",
            "text/markdown",
            origin_job_id=uuid4(),
        )
    with pytest.raises(ValueError, match="differ"):
        SourceReference(
            "conversion_result",
            uuid4(),
            owner,
            "a" * 64,
            "scan-pass",
            "text/markdown",
            uuid4(),
            uuid4(),
            "b" * 64,
        )
    with pytest.raises(ValueError, match="reverse result source media type"):
        SourceReference(
            "reversion_result",
            uuid4(),
            owner,
            "a" * 64,
            "scan-pass",
            "application/pdf",
            uuid4(),
            uuid4(),
            "a" * 64,
        )


def test_artifact_requires_complete_bytes_and_fixed_kind() -> None:
    with pytest.raises(ValueError, match="content"):
        ArtifactContent("preview", "text/html", b"")
    with pytest.raises(ValueError, match="kind"):
        ArtifactContent("script", "text/plain", b"content")
    with pytest.raises(ValueError, match="media type"):
        ArtifactContent("preview", "application/javascript", b"content")
