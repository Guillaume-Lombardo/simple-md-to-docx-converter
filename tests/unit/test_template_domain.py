"""Deterministic template identity and search-model tests."""

from uuid import uuid4

import pytest

from md_converter.templates.models import (
    TemplateIdentity,
    TemplateSearch,
    TemplateStatus,
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
