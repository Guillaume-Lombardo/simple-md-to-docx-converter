"""Architecture regressions for responsibility-bounded persistence adapters."""

import pytest

from markweave.jobs.ports import (
    JobCleanupRepository,
    JobLeaseRepository,
    JobQueryRepository,
    JobSubmissionRepository,
    JobTerminalRepository,
)
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.templates import SqlTemplateCatalogRepository
from markweave.templates.ports import (
    TemplateCatalogRepository,
    TemplateIdentityRepository,
    TemplatePublicationRecoveryRepository,
    TemplatePublicationRepository,
    TemplateSearchRepository,
    TemplateVersionQueryRepository,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("method", "module"),
    (
        ("create", "markweave.persistence.jobs.submission"),
        ("activate_source", "markweave.persistence.jobs.submission"),
        ("get", "markweave.persistence.jobs.queries"),
        ("list_owner", "markweave.persistence.jobs.queries"),
        ("claim", "markweave.persistence.jobs.claims"),
        ("heartbeat", "markweave.persistence.jobs.claims"),
        ("recover_expired_leases", "markweave.persistence.jobs.claims"),
        ("request_cancel", "markweave.persistence.jobs.lifecycle"),
        ("succeed", "markweave.persistence.jobs.lifecycle"),
        ("fail", "markweave.persistence.jobs.lifecycle"),
        ("finish_cancelled", "markweave.persistence.jobs.lifecycle"),
        ("expire_terminal", "markweave.persistence.jobs.cleanup"),
        ("complete_cleanup", "markweave.persistence.jobs.cleanup"),
    ),
)
def test_job_facade_delegates_to_bounded_modules(method: str, module: str) -> None:
    assert getattr(SqlJobRepository, method).__module__ == module


@pytest.mark.parametrize(
    ("method", "module"),
    (
        ("add", "markweave.persistence.templates.identity"),
        ("update_metadata", "markweave.persistence.templates.identity"),
        ("begin_delete", "markweave.persistence.templates.identity"),
        ("get", "markweave.persistence.templates.identity"),
        ("search", "markweave.persistence.templates.search"),
        ("reserve_create", "markweave.persistence.templates.publication"),
        ("finalize_version", "markweave.persistence.templates.publication"),
        (
            "claim_stale_pending",
            "markweave.persistence.templates.publication_recovery",
        ),
        ("pending_deletions", "markweave.persistence.templates.publication_recovery"),
        ("get_version", "markweave.persistence.templates.versions"),
        ("list_versions", "markweave.persistence.templates.versions"),
    ),
)
def test_template_facade_delegates_to_bounded_modules(method: str, module: str) -> None:
    assert getattr(SqlTemplateCatalogRepository, method).__module__ == module


def test_provider_neutral_ports_are_split_by_responsibility() -> None:
    assert set(JobSubmissionRepository.__dict__) >= {"create", "activate_source"}
    assert set(JobQueryRepository.__dict__) >= {"get", "list_owner"}
    assert set(JobLeaseRepository.__dict__) >= {"claim", "heartbeat"}
    assert set(JobTerminalRepository.__dict__) >= {"succeed", "fail"}
    assert set(JobCleanupRepository.__dict__) >= {
        "expire_terminal",
        "complete_cleanup",
    }
    assert _public_methods(TemplateIdentityRepository) == {
        "add",
        "get",
        "update_metadata",
        "set_status",
        "delete_guarded",
        "begin_delete",
        "finalize_delete",
    }
    assert _public_methods(TemplateSearchRepository) == {"search"}
    assert _public_methods(TemplatePublicationRepository) == {
        "create_versioned",
        "reserve_create",
        "reserve_version",
        "finalize_version",
        "publish_version",
    }
    assert _public_methods(TemplatePublicationRecoveryRepository) == {
        "abort_pending",
        "claim_stale_pending",
        "release_pending_claim",
        "pending_deletions",
    }
    assert _public_methods(TemplateVersionQueryRepository) == {
        "get_version",
        "list_versions",
    }
    assert _public_methods(TemplateCatalogRepository) == set()


def _public_methods(protocol: type[object]) -> set[str]:
    return {
        name
        for name, value in protocol.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
