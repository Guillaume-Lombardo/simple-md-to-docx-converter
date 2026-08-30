"""Composed SQL implementation of the provider-neutral template catalog ports."""

from markweave.persistence.templates.identity import _TemplateIdentityRepository
from markweave.persistence.templates.publication import (
    _TemplatePublicationRepository,
)
from markweave.persistence.templates.publication_recovery import (
    _TemplatePublicationRecoveryRepository,
)
from markweave.persistence.templates.search import _TemplateSearchRepository
from markweave.persistence.templates.versions import _TemplateVersionQueryRepository


class SqlTemplateCatalogRepository(
    _TemplateIdentityRepository,
    _TemplatePublicationRepository,
    _TemplatePublicationRecoveryRepository,
    _TemplateVersionQueryRepository,
    _TemplateSearchRepository,
):
    """Template catalog composed from responsibility-bounded SQL stores."""
