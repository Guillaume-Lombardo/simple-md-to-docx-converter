"""Deterministic template identity and search-model tests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from md_converter.templates.models import (
    TemplateIdentity,
    TemplatePublicationState,
    TemplateSearch,
    TemplateStatus,
    TemplateVersion,
    normalize_template_text,
)


@pytest.mark.unit
def test_template_identity_normalization_is_unicode_stable() -> None:
    template = TemplateIdentity(
        uuid4(),
        uuid4(),
        "  \uff26\uff49\uff4e\uff41\uff4e\uff43\uff45  ",
        "  Quarterly RÉSUMÉ  ",
        TemplateStatus.ACTIVE,
    )
    assert template.normalized_name == "finance"
    assert template.normalized_description == "quarterly résumé"
    assert normalize_template_text("  A\u212a  ") == "ak"


@pytest.mark.unit
def test_template_identity_and_pagination_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="name must not be blank"):
        TemplateIdentity(uuid4(), uuid4(), "　", "description", TemplateStatus.ACTIVE)
    with pytest.raises(ValueError, match="offset"):
        TemplateSearch(offset=-1)
    with pytest.raises(ValueError, match="limit"):
        TemplateSearch(limit=0)
    with pytest.raises(ValueError, match="revision"):
        TemplateIdentity(
            uuid4(), uuid4(), "name", "description", TemplateStatus.ACTIVE, 0
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("number", "size", "digest", "created_at"),
    [
        (0, 1, "a" * 64, datetime.now(UTC)),
        (1, 0, "a" * 64, datetime.now(UTC)),
        (1, 1, "short", datetime.now(UTC)),
        (1, 1, "z" * 64, datetime.now(UTC)),
        (1, 1, "a" * 64, datetime.now(UTC).replace(tzinfo=None)),
    ],
)
def test_template_versions_reject_invalid_immutable_metadata(
    number: int, size: int, digest: str, created_at: datetime
) -> None:
    with pytest.raises(ValueError):
        TemplateVersion(
            uuid4(),
            uuid4(),
            number,
            uuid4(),
            digest,
            size,
            created_at,
            uuid4(),
        )


@pytest.mark.unit
def test_pending_template_version_requires_timezone_aware_complete_lease() -> None:
    values = (
        uuid4(),
        uuid4(),
        1,
        uuid4(),
        "a" * 64,
        1,
        datetime.now(UTC),
        uuid4(),
    )
    with pytest.raises(ValueError, match="require a publication lease"):
        TemplateVersion(
            *values,
            publication_state=TemplatePublicationState.PENDING,
        )
    with pytest.raises(ValueError, match="lease timestamp"):
        TemplateVersion(
            *values,
            publication_state=TemplatePublicationState.PENDING,
            publication_token=uuid4(),
            publication_lease_expires_at=datetime.now(UTC).replace(tzinfo=None),
        )
